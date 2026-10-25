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

    private SensorManager sensorManager;
    private Sensor counterSensor, detectorSensor, accelerationSensor;
    private boolean hasCounter=false, hasDetector=false, hasAccel=false;
    private boolean accelRegistered=false, screenReceiverRegistered=false;
    private String sessionDay="";
    private int counterSteps=0, quickSteps=0, detectorSteps=0, sessionPublished=0;
    private float gx,gy,gz;
    private boolean gravityReady=false, peakHigh=false;
    private long lastPeakMs=0L;
    private long lastNotification=0L;
    private int notifiedSteps=-1;
    private final Handler notifyHandler=new Handler(Looper.getMainLooper());
    private final Runnable flushNotification=()->sendStepNotification();

    private final BroadcastReceiver screenReceiver=new BroadcastReceiver(){
        @Override public void onReceive(Context context,Intent intent){
// Ekran acilip kapaninca olcum modu degismez.
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        sensorManager=(SensorManager)getSystemService(SENSOR_SERVICE);
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"Canlı adım sayımı",NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(Build.VERSION.SDK_INT>=29 && checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)!=PackageManager.PERMISSION_GRANTED){
            stopSelf();return START_NOT_STICKY;
        }
        try{startForeground(5,notification());}
        catch(Exception ex){
            android.util.Log.e("AdimPlus","Ön planda adım servisi başlatılamadı",ex);
            stopSelf();return START_NOT_STICKY;
        }

        if(!isRunning) {
            isRunning=true;
            sessionDay=StepStore.day(0);
            counterSensor=sensorManager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
            detectorSensor=sensorManager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR);
            // v1.4: ivmeolcer kullanilmiyor. Elde oynatma adim sayilmayacak.
            // Eski sürüm dedektörü tek kaynak olarak seçiyordu: bazı telefonlarda hiç olay gelmiyordu.
            // Önce donanım sayacı, ayrıca düşük gecikmeli hareket verisi kullanılır.
            if(counterSensor!=null)
                hasCounter=sensorManager.registerListener(this,counterSensor,SensorManager.SENSOR_DELAY_FASTEST,0);
            if(detectorSensor!=null)
                hasDetector=sensorManager.registerListener(this,detectorSensor,SensorManager.SENSOR_DELAY_FASTEST,0);
            // Yalniz donanim adim sayaci ve dedektorunu kaydet.
            try{
                IntentFilter filter=new IntentFilter();
                filter.addAction(Intent.ACTION_SCREEN_ON);
                filter.addAction(Intent.ACTION_SCREEN_OFF);
                registerReceiver(screenReceiver,filter);
                screenReceiverRegistered=true;
            }catch(Exception ex){
                android.util.Log.w("AdimPlus","Ekran alıcısı kaydedilemedi",ex);
            }
            isSensorReady=hasCounter||hasDetector;
            sensorMode=hasCounter&&hasDetector?"Donanım adım sayacı + algılayıcı":
                       hasCounter?"Donanım adım sayacı":
                       hasDetector?"Donanım adım algılayıcı":"Uyumlu adım sensörü yok";
            sendBroadcast(new Intent(UPDATE_ACTION).setPackage(getPackageName()));
        }
        return START_STICKY;
    }

    private void setAccelEnabled(boolean enable){
        if(accelerationSensor==null || sensorManager==null)return;
        if(enable && !accelRegistered){
            hasAccel=sensorManager.registerListener(this,accelerationSensor,SensorManager.SENSOR_DELAY_GAME,0);
            accelRegistered=hasAccel;
            gravityReady=false;peakHigh=false;
        }else if(!enable && accelRegistered){
            sensorManager.unregisterListener(this,accelerationSensor);
            accelRegistered=false;
        }
    }

    private void checkDay(){
        String today=StepStore.day(0);
        if(today.equals(sessionDay))return;
        sessionDay=today;
        counterSteps=0;quickSteps=0;detectorSteps=0;sessionPublished=0;
        notifiedSteps=-1;
    }

    @Override public void onSensorChanged(SensorEvent event){
        if(event==null||event.sensor==null||event.values.length==0)return;
        checkDay();
        int type=event.sensor.getType();
        if(type==Sensor.TYPE_STEP_COUNTER){
            int delta=StepStore.ingestCounter(this,event.values[0]);
            if(delta>0)counterSteps=Math.min(1000000,counterSteps+delta);
            lastEventMs=SystemClock.elapsedRealtime();
        }else if(type==Sensor.TYPE_STEP_DETECTOR){
            detectorSteps++;
            lastEventMs=SystemClock.elapsedRealtime();
        }else if(type==Sensor.TYPE_ACCELEROMETER){
            detectWalkingMotion(event.values);
        }else return;
        reconcileCounts();
    }

    private void detectWalkingMotion(float[] v){
        if(v.length<3)return;
        float x=v[0],y=v[1],z=v[2];
        if(!gravityReady){
            gx=x;gy=y;gz=z;gravityReady=true;
            return;
        }
        final float alpha=0.87f;
        gx=alpha*gx+(1f-alpha)*x;
        gy=alpha*gy+(1f-alpha)*y;
        gz=alpha*gz+(1f-alpha)*z;
        float dx=x-gx,dy=y-gy,dz=z-gz;
        float movement=(float)Math.sqrt(dx*dx+dy*dy+dz*dz);
        long now=SystemClock.elapsedRealtime();
        if(movement<0.62f)peakHigh=false;
        if(!peakHigh && movement>1.35f && movement<14f && now-lastPeakMs>310){
            peakHigh=true;
            lastPeakMs=now;
            quickSteps=Math.min(1000000,quickSteps+1);
            lastEventMs=now;
        }
    }

    private void reconcileCounts(){
        // Üç farklı sensörü ayrı ayrı TOPLAMAYIZ: aksi halde aynı adım 2 kez sayılır.
        // Donanım sayacı gecikirse hareket sensörü geçici olarak rakamı yükseltir.
        int best=Math.max(counterSteps,Math.max(detectorSteps,quickSteps));
        if(best<=sessionPublished)return;
        int delta=best-sessionPublished;
        sessionPublished=best;
        StepStore.add(this,delta);
        sendBroadcast(new Intent(UPDATE_ACTION).setPackage(getPackageName()));
        checkGoal();
        long now=SystemClock.elapsedRealtime();
        if(now-lastNotification>=700L && notifiedSteps!=StepStore.steps(this,0))
            sendStepNotification();
        notifyHandler.removeCallbacks(flushNotification);
        notifyHandler.postDelayed(flushNotification,850L);
    }

    private void checkGoal(){
        int today=StepStore.steps(this,0);
        String date=StepStore.day(0);
        if(today<StepStore.goal(this) || date.equals(StepStore.prefs(this).getString("goal_alert","")))return;
        StepStore.prefs(this).edit().putString("goal_alert",date).apply();
        try{
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(6,
                new Notification.Builder(this,CHANNEL)
                    .setContentTitle("Tebrikler! Hedefine ulaştın")
                    .setContentText(today+" adım attın, devam et!")
                    .setSmallIcon(R.drawable.ic_steps)
                    .setAutoCancel(true).build());
        }catch(Exception ignored){}
    }

    private void sendStepNotification(){
        int today=StepStore.steps(this,0);
        if(today==notifiedSteps)return;
        try{
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(5,notification());
            notifiedSteps=today;
            lastNotification=SystemClock.elapsedRealtime();
        }catch(Exception e){android.util.Log.w("AdimPlus","Bildirim güncellenemedi",e);}
    }
    private Notification notification(){
        int n=StepStore.steps(this,0),g=StepStore.goal(this);
        PendingIntent pending=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL)
            .setSmallIcon(R.drawable.ic_steps)
            .setContentTitle("Adım+ • Adım takibi açık")
            .setContentText(String.format(new Locale("tr","TR"),"%,d / %,d adım • %d%% tamamlandı",n,g,n*100/Math.max(1,g)))
            .setContentIntent(pending)
            .setOngoing(true).setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE).build();
    }
    @Override public void onAccuracyChanged(Sensor sensor,int accuracy){}
    @Override public void onDestroy(){
        notifyHandler.removeCallbacksAndMessages(null);
        if(sensorManager!=null)sensorManager.unregisterListener(this);
        if(screenReceiverRegistered){
            try{unregisterReceiver(screenReceiver);}catch(Exception ignored){}
            screenReceiverRegistered=false;
        }
        isRunning=false;isSensorReady=false;lastEventMs=0L;
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
