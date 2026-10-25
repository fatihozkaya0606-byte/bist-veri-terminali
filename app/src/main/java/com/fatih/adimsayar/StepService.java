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
    public static final String WALK_ON="com.fatih.adimsayar.WALK_ON";
    public static final String WALK_OFF="com.fatih.adimsayar.WALK_OFF";
    public static volatile boolean isWalkingMode=false;
    public static volatile boolean isRunning=false;
    public static volatile boolean isSensorReady=false;
    public static volatile long lastEventMs=0L;
    public static volatile String sensorMode="";
    public static volatile int counterEvents=0,detectorEvents=0,gaitPeaks=0;
    public static volatile int confidence=0;
    public static volatile String lastSource="Veri bekleniyor";
    public static volatile int hardwareStepsReceived=0;

    private SensorManager manager;
    private Sensor stepCounter,stepDetector,accelerometer;
    private boolean counterRegistered=false,detectorRegistered=false,accelRegistered=false;
    private String sessionDay="";
    private int counterSteps=0,detectorSteps=0,rhythmSteps=0,sessionPublished=0;
    private long lastNotification=0L;
    private int notifiedSteps=-1;
    private final Handler notifyHandler=new Handler(Looper.getMainLooper());
    private final Runnable notifyFlush=()->sendStepNotification();

    // Yön bağımsız, imzalı dikey ivme sinyali: v1.5'teki tek tetiklemede kilitlenmeyi giderir.
    private float gx,gy,gz,filteredVertical=0f,olderVertical=0f,oldVertical=0f;
    private boolean gravReady=false;
    public static volatile int motionSamples=0;
    public static volatile float signalPeak=0f;
    private long lastPeak=0L;
    private int candidateRun=0;
    private int lastInterval=0;
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
        if(isRunning){
            if(intent!=null && WALK_ON.equals(intent.getAction()))setWalkingSession(true);
            else if(intent!=null && WALK_OFF.equals(intent.getAction()))setWalkingSession(false);
            return START_STICKY;
        }
        if(!isRunning){
            isRunning=true;
            sessionDay=StepStore.day(0);
            counterEvents=0;detectorEvents=0;gaitPeaks=0;confidence=0;motionSamples=0;signalPeak=0f;
            lastSource="Sensör olayları bekleniyor";
            stepCounter=manager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
            stepDetector=manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR);
            accelerometer=manager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            if(stepCounter!=null)
                counterRegistered=manager.registerListener(this,stepCounter,SensorManager.SENSOR_DELAY_FASTEST,0);
            if(stepDetector!=null)
                detectorRegistered=manager.registerListener(this,stepDetector,SensorManager.SENSOR_DELAY_FASTEST,0);
            // Varsayilan: SADECE fiziksel adim sensorleri. Telefonu oynatmak adim degildir.
            // Ivmeolcer asla otomatik olarak sayim kaynagina kaydedilmez.
            isWalkingMode=false;
            isSensorReady=counterRegistered||detectorRegistered;
            sensorMode=counterRegistered&&detectorRegistered?"Donanım sayacı + adım algılayıcı":
                counterRegistered?"Donanım adım sayacı":
                detectorRegistered?"Donanım adım algılayıcı":"Donanım adım sensörü yok";
            if(intent!=null && WALK_ON.equals(intent.getAction()))setWalkingSession(true);
            notifyUi();
        }
        return START_STICKY;
    }

    private void setWalkingSession(boolean enabled){
        // Hizli ivmeolcer modu sadece kullanici acikca baslatinca kullanilir.
        if(enabled && accelerometer==null){
            lastSource="Bu telefonda hizli hareket sensoru yok";
            return;
        }
        if(enabled && !isWalkingMode) {
            // Other sensors already counted the earlier steps; start fast mode from
            // current published total instead of waiting to catch up from zero.
            rhythmSteps=Math.max(rhythmSteps,sessionPublished);
            candidateRun=0;lastInterval=0;
        }
        isWalkingMode=enabled;
        enableMotion(enabled);
        StepStore.prefs(this).edit().putBoolean("walking_session",enabled).apply();
        isSensorReady=counterRegistered||detectorRegistered||accelRegistered;
        sensorMode=enabled?"Canlı yürüyüş (isteğe bağlı)":"Donanım adım sayacı";
        lastSource=enabled?"Yürüyüş başlatıldı":"Otomatik donanım sayımı";
        notifyUi();
    }
    private void enableMotion(boolean enable){
        if(manager==null||accelerometer==null)return;
        if(enable&&!accelRegistered){
            accelRegistered=manager.registerListener(this,accelerometer,SensorManager.SENSOR_DELAY_GAME,0);
            gravReady=false;filteredVertical=0f;olderVertical=0f;oldVertical=0f;
            candidateRun=0;lastPeak=0;lastInterval=0;motionSamples=0;signalPeak=0f;
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
                hardwareStepsReceived+=increment;
                lastSource="Donanım adım sayacı";
                lastEventMs=SystemClock.elapsedRealtime();
            }
        }else if(type==Sensor.TYPE_STEP_DETECTOR){
            detectorEvents++;
            if(event.values[0]>0f){
                detectorSteps++;
                hardwareStepsReceived++;
                lastSource="Donanım adım algılayıcı";
                lastEventMs=SystemClock.elapsedRealtime();
            }
        }else if(type==Sensor.TYPE_ACCELEROMETER){
            if(!isWalkingMode)return;
            detectRhythmicWalking(event.values);
        }else return;
        mergeSteps();
    }

    // Donanımdan adım gelmiyorsa, sadece ardışık düzenli yürüyüş ritmi için yedek sayım.
    // Her sensör ölçümünde yükselip düşen ivme tepesini algılar: önceki sürümde
    // eşik altına dönemeyen sinyal sayımı kilitliyordu.
    private void detectRhythmicWalking(float[] v){
        if(v.length<3)return;
        motionSamples++;
        float x=v[0],y=v[1],z=v[2];
        if(!gravReady){gx=x;gy=y;gz=z;gravReady=true;return;}
        final float alpha=0.90f;
        gx=alpha*gx+(1f-alpha)*x;
        gy=alpha*gy+(1f-alpha)*y;
        gz=alpha*gz+(1f-alpha)*z;
        float g=(float)Math.sqrt(gx*gx+gy*gy+gz*gz);
        if(g<3f)return;
        float projected=((x-gx)*gx+(y-gy)*gy+(z-gz)*gz)/g;
        filteredVertical=filteredVertical*0.58f+projected*0.42f;
        float threshold=StepStore.prefs(this).getInt("sensitivity",1)==2?0.32f:
                        StepStore.prefs(this).getInt("sensitivity",1)==0?0.88f:0.47f;
        long now=SystemClock.elapsedRealtime();
        if(lastPeak!=0 && now-lastPeak>1900L && candidateRun>0){
            candidateRun=0;confidence=0;lastInterval=0;
            lastSource="Yeni yürüyüş serisi bekleniyor";
        }
        // Pozitif yerel tepe; tekrar sıfıra dönmesini şart koşmayız.
        boolean peak=oldVertical>olderVertical &&
                     oldVertical>=filteredVertical &&
                     oldVertical>threshold &&
                     oldVertical<8.0f &&
                     (lastPeak==0||now-lastPeak>=280L);
        olderVertical=oldVertical;
        oldVertical=filteredVertical;
        if(!peak)return;
        signalPeak=oldVertical;
        gaitPeaks++;
        long interval=lastPeak==0?0:now-lastPeak;
        if(interval>=320L&&interval<=1170L){
            if(lastInterval==0||Math.abs((int)interval-lastInterval)<=330)
                candidateRun=Math.min(50,candidateRun+1);
            else
                candidateRun=1;
            lastInterval=(int)interval;
        }else{
            candidateRun=1;
            lastInterval=0;
        }
        lastPeak=now;
        confidence=Math.min(100,candidateRun*33);
        lastSource="Ritim denetimi "+Math.min(candidateRun,3)+"/3";
        // Fast mode is OPT-IN, requiring three regular peaks rather than four.
        if(candidateRun>=3){
            rhythmSteps=Math.min(1000000,rhythmSteps+(candidateRun==3?3:1));
            lastSource="Düzenli yürüyüş algılandı";
            lastEventMs=now;
        }
    }

    private void mergeSteps(){
        // Dedektör, sayaç ve ritimden en yüksek ilerleme alınır; toplamları alınmaz.
        int maximum=isWalkingMode?Math.max(counterSteps,Math.max(detectorSteps,rhythmSteps)):Math.max(counterSteps,detectorSteps);
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
        isWalkingMode=false;
        isRunning=false;isSensorReady=false;lastEventMs=0;
        StepStore.prefs(this).edit().putBoolean("walking_session",false).apply();
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
