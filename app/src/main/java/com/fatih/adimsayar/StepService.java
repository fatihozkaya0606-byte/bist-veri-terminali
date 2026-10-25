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
    // Stable fields for diagnostics and status; fast acceleration counting is disabled.
    public static volatile boolean isWalkingMode=false;
    public static volatile boolean isRunning=false;
    public static volatile boolean isSensorReady=false;
    public static volatile String sensorMode="Donanım adım sayacı";
    public static volatile String lastSource="Adım sensörü bekleniyor";
    public static volatile long lastEventMs=0L;
    public static volatile int counterEvents=0;
    public static volatile int detectorEvents=0;
    public static volatile int motionSamples=0;
    public static volatile int gaitPeaks=0;
    public static volatile int confidence=0;
    public static volatile float signalPeak=0f;
    private final Handler notifyHandler=new Handler(Looper.getMainLooper());
    private final Runnable notificationFlush=()->updateNotification();
    private void updateNotification(){
        int total=StepStore.steps(this,0);
        if(total==notifiedSteps)return;
        try{
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(5,notification());
            notifiedSteps=total;lastNotification=SystemClock.elapsedRealtime();
        }catch(Exception e){android.util.Log.w("AdimPlus","Adım bildirimi",e);}
    }

    private SensorManager manager;
    private Sensor sensor;
    private boolean counter;
    private long lastNotification=0;
    private int notifiedSteps=-1;
    private boolean reached=false;
    @Override public void onCreate() {
        super.onCreate();
        manager=(SensorManager)getSystemService(SENSOR_SERVICE);
        NotificationManager n=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        n.createNotificationChannel(new NotificationChannel(CHANNEL,"Adım sayımı",NotificationManager.IMPORTANCE_LOW));
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(android.os.Build.VERSION.SDK_INT>=29 && checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)!=PackageManager.PERMISSION_GRANTED){
            stopSelf(); return START_NOT_STICKY;
        }
        try {startForeground(5,notification());}catch(Exception ex){stopSelf();return START_NOT_STICKY;}
        if(sensor==null) {
            sensor=manager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
            counter=sensor!=null;
            if(sensor==null) sensor=manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR);
            if(sensor!=null){
                isSensorReady=manager.registerListener(this,sensor,SensorManager.SENSOR_DELAY_NORMAL);
                sensorMode=counter?"Donanım adım sayacı":"Donanım adım algılayıcı";
                lastSource=isSensorReady?"Sensör verisi bekleniyor":"Adım sensörü başlatılamadı";
            }
            isRunning=isSensorReady;
            sendBroadcast(new Intent(UPDATE_ACTION).setPackage(getPackageName()));
        }
        return START_STICKY;
    }
    @Override public void onSensorChanged(SensorEvent event) {
        if(event.values.length==0)return;
        int before=StepStore.steps(this,0);
        if(counter){
            counterEvents++;
            int change=StepStore.ingestCounter(this,event.values[0]);
            if(change>0)lastSource="Donanım adım sayacı";
        } else {
            detectorEvents++;
            if(event.values[0]>0f){
                StepStore.add(this,1);
                lastSource="Donanım adım algılayıcı";
            }
        }
        int after=StepStore.steps(this,0);
        if(after!=before){
            lastEventMs=SystemClock.elapsedRealtime();
            sendBroadcast(new Intent(UPDATE_ACTION).setPackage(getPackageName()));
            long now=SystemClock.elapsedRealtime();
            if(now-lastNotification>=700)updateNotification();
            notifyHandler.removeCallbacks(notificationFlush);
            notifyHandler.postDelayed(notificationFlush,800);
        }
        int today=StepStore.steps(this,0);
        if(today>=StepStore.goal(this) && !reached && !StepStore.prefs(this).getString("goal_alert","").equals(StepStore.day(0))) {
            reached=true;
            StepStore.prefs(this).edit().putString("goal_alert",StepStore.day(0)).apply();
            NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            nm.notify(6,new Notification.Builder(this,CHANNEL).setContentTitle("Günlük hedef tamamlandı!")
                .setContentText(String.format(new Locale("tr","TR"),"%s adım! Harika gidiyorsun.",today))
                .setSmallIcon(R.drawable.ic_steps).setAutoCancel(true).build());
        }

    }
    private Notification notification() {
        int n=StepStore.steps(this,0),g=StepStore.goal(this);
        Intent go=new Intent(this,MainActivity.class);
        PendingIntent pending=PendingIntent.getActivity(this,0,go,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL)
            .setSmallIcon(R.drawable.ic_steps).setContentTitle("Adım+ • Donanım adım takibi")
            .setContentText(String.format(new Locale("tr","TR"),"%d / %d adım • Hedef %%%d",n,g,Math.min(999,n*100/Math.max(1,g))))
            .setContentIntent(pending).setOngoing(true).setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE).build();
    }
    @Override public void onAccuracyChanged(Sensor s,int accuracy) {}
    @Override public void onDestroy() {
        notifyHandler.removeCallbacksAndMessages(null);
        if(manager!=null)manager.unregisterListener(this);
        isRunning=false;isSensorReady=false;isWalkingMode=false;
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) {return null;}
}
