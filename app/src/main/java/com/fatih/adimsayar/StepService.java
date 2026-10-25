package com.fatih.adimsayar;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.hardware.*;
import android.os.*;
import java.util.Locale;

public class StepService extends Service implements SensorEventListener {
    public static final String CHANNEL="adim_takip";
    public static final String UPDATE_ACTION="com.fatih.adimsayar.STEP_UPDATED";
    public static volatile boolean isRunning=false;
    public static volatile boolean isSensorReady=false;
    public static volatile long lastEventMs=0L;
    public static volatile String sensorMode="";
    public static volatile int counterEvents=0,detectorEvents=0,gaitPeaks=0;
    public static volatile int confidence=0;
    public static volatile String lastSource="Veri bekleniyor";

    private SensorManager manager;
    private Sensor stepCounter,stepDetector,accelerometer;
    private boolean counterRegistered=false,detectorRegistered=false,accelRegistered=false,screenRegistered=false;
    private String sessionDay="";
    private int counterSteps=0,detectorSteps=0,rhythmSteps=0,sessionPublished=0;
    private long lastNotification=0L;
    private int notifiedSteps=-1;
    private final Handler notifyHandler=new Handler(Looper.getMainLooper());
    private final Runnable notifyFlush=()->sendStepNotification();

    private float gx,gy,gz,smoothed=0f;
    private boolean gravReady=false,peakActive=false;
    private long lastPeak=0L;
    private int candidateRun=0;
    private int lastInterval=0;
    private final BroadcastReceiver screenState=new BroadcastReceiver(){
        @Override public void onReceive(Context c,Intent intent){
            if(Intent.ACTION_SCREEN_ON.equals(intent.getAction()))enableMotion(true);
            if(Intent.ACTION_SCREEN_OFF.equals(intent.getAction()) && (counterRegistered||detectorRegistered))
                enableMotion(false);
        }
    };

    @Override public void onCreate(){
        super.onCreate();
        manager=(SensorManager)getSystemService(SENSOR_SERVICE);
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(
            new NotificationChannel(CHANNEL,"Adım+ takip",NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(Build.VERSION.SDK_INT>=29 &&
            checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)!=PackageManager.PERMISSION_GRANTED){
            stopSelf();return START_NOT_STICKY;
        }
        try{startForeground(5,notification());}
        catch(Exception ex){
            android.util.Log.e("AdimPlus","Adım servisi başlatılamadı",ex);
            stopSelf();return START_NOT_STICKY;
        }
        if(!isRunning){
            isRunning=true;
            sessionDay=StepStore.day(0);
            counterEvents=0;detectorEvents=0;gaitPeaks=0;confidence=0;
            lastSource="Sensör olayları bekleniyor";
            stepCounter=manager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
            stepDetector=manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR);
            accelerometer=manager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            if(stepCounter!=null)
                counterRegistered=manager.registerListener(this,stepCounter,SensorManager.SENSOR_DELAY_NORMAL,0);
            if(stepDetector!=null)
                detectorRegistered=manager.registerListener(this,stepDetector,SensorManager.SENSOR_DELAY_NORMAL,0);
            PowerManager pm=(PowerManager)getSystemService(POWER_SERVICE);
            if(pm==null||pm.isInteractive()||(!counterRegistered&&!detectorRegistered))enableMotion(true);
            IntentFilter filter=new IntentFilter();
            filter.addAction(Intent.ACTION_SCREEN_ON);
            filter.addAction(Intent.ACTION_SCREEN_OFF);
            try{registerReceiver(screenState,filter);screenRegistered=true;}catch(Exception e){android.util.Log.w("AdimPlus","Ekran durumu",e);}
            isSensorReady=counterRegistered||detectorRegistered||accelRegistered;
            sensorMode=counterRegistered&&detectorRegistered?"Adım sayacı + dedektör + yürüyüş doğrulama":
                counterRegistered?"Adım sayacı + yürüyüş doğrulama":
                detectorRegistered?"Adım dedektörü + yürüyüş doğrulama":
                accelRegistered?"Ritmik yürüyüş doğrulama":"Kullanılabilir sensör yok";
            notifyUi();
        }
        return START_STICKY;
    }

    private void enableMotion(boolean enable){
        if(manager==null||accelerometer==null)return;
        if(enable&&!accelRegistered){
            accelRegistered=manager.registerListener(this,accelerometer,SensorManager.SENSOR_DELAY_GAME,0);
            gravReady=false;peakActive=false;candidateRun=0;lastPeak=0;
        }else if(!enable&&accelRegistered){
            manager.unregisterListener(this,accelerometer);accelRegistered=false;
            candidateRun=0;confidence=0;
        }
    }

    private void checkDay(){
        String nowDay=StepStore.day(0);
        if(nowDay.equals(sessionDay))return;
        sessionDay=nowDay;
        counterSteps=0;detectorSteps=0;rhythmSteps=0;sessionPublished=0;
        candidateRun=0;notifiedSteps=-1;
    }

    @Override public void onSensorChanged(SensorEvent event){
        if(event==null||event.sensor==null||event.values.length==0)return;
        checkDay();
        int type=event.sensor.getType();
        if(type==Sensor.TYPE_STEP_COUNTER){
            counterEvents++;
            int increment=StepStore.ingestCounter(this,event.values[0]);
            if(increment>0){
                counterSteps+=increment;
                lastSource="Donanım adım sayacı";
                lastEventMs=SystemClock.elapsedRealtime();
            }
        }else if(type==Sensor.TYPE_STEP_DETECTOR){
            detectorEvents++;
            if(event.values[0]>0f){
                detectorSteps++;
                lastSource="Donanım adım algılayıcı";
                lastEventMs=SystemClock.elapsedRealtime();
            }
        }else if(type==Sensor.TYPE_ACCELEROMETER){
            detectRhythmicWalking(event.values);
        }else return;
        mergeSteps();
    }

    // Tek seferlik sallama adım DEĞİLDİR. Art arda düzenli yürüyüş ritmi aranır.
    private void detectRhythmicWalking(float[] v){
        if(v.length<3)return;
        float x=v[0],y=v[1],z=v[2];
        if(!gravReady){gx=x;gy=y;gz=z;gravReady=true;return;}
        gx=gx*0.86f+x*0.14f;
        gy=gy*0.86f+y*0.14f;
        gz=gz*0.86f+z*0.14f;
        float dx=x-gx,dy=y-gy,dz=z-gz;
        float linear=(float)Math.sqrt(dx*dx+dy*dy+dz*dz);
        smoothed=0.3f*linear+0.7f*smoothed;
        if(smoothed<0.68f)peakActive=false;
        if(peakActive||smoothed<1.65f||smoothed>10f)return;
        peakActive=true;
        long now=SystemClock.elapsedRealtime();
        long interval=lastPeak==0?0:now-lastPeak;
        if(lastPeak!=0&&interval<300)return;
        gaitPeaks++;
        if(interval>=350 && interval<=930){
            int variation=lastInterval==0?0:Math.abs((int)interval-lastInterval);
            if(lastInterval==0||variation<235){
                candidateRun++;
            }else{
                candidateRun=1;
            }
            lastInterval=(int)interval;
        }else{
            candidateRun=1;lastInterval=0;
        }
        lastPeak=now;
        confidence=Math.min(100,candidateRun*20);
        // 5 birbirine benzer ritmik adim olmadan SAYI DEGISMEZ.
        if(candidateRun>=5){
            int number=candidateRun==5?5:1;
            rhythmSteps=Math.min(1000000,rhythmSteps+number);
            lastSource="Doğrulanmış yürüyüş ritmi";
            lastEventMs=now;
        }
    }

    private void mergeSteps(){
        // Dedektör, sayaç ve ritimden en yüksek ilerleme alınır; toplamları alınmaz.
        int maximum=Math.max(counterSteps,Math.max(detectorSteps,rhythmSteps));
        int increment=maximum-sessionPublished;
        if(increment<=0)return;
        sessionPublished=maximum;
        StepStore.add(this,increment);
        notifyUi();
        notifyGoal();
        long now=SystemClock.elapsedRealtime();
        if(now-lastNotification>=850L)sendStepNotification();
        notifyHandler.removeCallbacks(notifyFlush);
        notifyHandler.postDelayed(notifyFlush,850);
    }

    private void notifyUi(){
        sendBroadcast(new Intent(UPDATE_ACTION).setPackage(getPackageName()));
    }

    private void notifyGoal(){
        int steps=StepStore.steps(this,0);
        String day=StepStore.day(0);
        if(steps<StepStore.goal(this)||day.equals(StepStore.prefs(this).getString("goal_alert","")))return;
        StepStore.prefs(this).edit().putString("goal_alert",day).apply();
        try{
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(6,
                new Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.ic_steps)
                    .setContentTitle("Günlük hedefe ulaştın!")
                    .setContentText(steps+" adım kaydedildi")
                    .setAutoCancel(true).build());
        }catch(Exception ignored){}
    }

    private void sendStepNotification(){
        int steps=StepStore.steps(this,0);
        if(steps==notifiedSteps)return;
        try{
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(5,notification());
            lastNotification=SystemClock.elapsedRealtime();notifiedSteps=steps;
        }catch(Exception e){android.util.Log.w("AdimPlus","Bildirim yenilenemedi",e);}
    }
    private Notification notification(){
        int steps=StepStore.steps(this,0),goal=StepStore.goal(this);
        PendingIntent p=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.ic_steps)
            .setContentTitle("Adım+ • Yürüyüş takibi")
            .setContentText(String.format(new Locale("tr","TR"),"%,d / %,d adım • %d%% tamamlandı",
                steps,goal,steps*100/Math.max(goal,1)))
            .setContentIntent(p).setOnlyAlertOnce(true).setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE).build();
    }
    @Override public void onAccuracyChanged(Sensor sensor,int value){}
    @Override public void onDestroy(){
        notifyHandler.removeCallbacksAndMessages(null);
        if(manager!=null)manager.unregisterListener(this);
        if(screenRegistered){try{unregisterReceiver(screenState);}catch(Exception ignored){}}
        isRunning=false;isSensorReady=false;lastEventMs=0;
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
