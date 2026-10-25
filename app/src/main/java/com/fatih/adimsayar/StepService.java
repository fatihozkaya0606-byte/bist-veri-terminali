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
            if(sensor!=null)manager.registerListener(this,sensor,SensorManager.SENSOR_DELAY_NORMAL);
        }
        return START_STICKY;
    }
    @Override public void onSensorChanged(SensorEvent event) {
        if(event.values.length==0)return;
        if(counter)StepStore.ingestCounter(this,event.values[0]);
        else StepStore.add(this,1);
        int today=StepStore.steps(this,0);
        if(today>=StepStore.goal(this) && !reached && !StepStore.prefs(this).getString("goal_alert","").equals(StepStore.day(0))) {
            reached=true;
            StepStore.prefs(this).edit().putString("goal_alert",StepStore.day(0)).apply();
            NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            nm.notify(6,new Notification.Builder(this,CHANNEL).setContentTitle("Günlük hedef tamamlandı!")
                .setContentText(String.format(new Locale("tr","TR"),"%s adım! Harika gidiyorsun.",today))
                .setSmallIcon(R.drawable.ic_steps).setAutoCancel(true).build());
        }
        long now=SystemClock.elapsedRealtime();
        if(now-lastNotification>60000 || Math.abs(today-notifiedSteps)>=100){
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(5,notification());
            lastNotification=now;notifiedSteps=today;
        }
    }
    private Notification notification() {
        int n=StepStore.steps(this,0),g=StepStore.goal(this);
        Intent go=new Intent(this,MainActivity.class);
        PendingIntent pending=PendingIntent.getActivity(this,0,go,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL)
            .setSmallIcon(R.drawable.ic_steps).setContentTitle("Adım+ • Adım takibi açık")
            .setContentText(String.format(new Locale("tr","TR"),"%d / %d adım • Hedef%%%d",n,g,Math.min(999,n*100/Math.max(1,g))))
            .setContentIntent(pending).setOngoing(true).setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE).build();
    }
    @Override public void onAccuracyChanged(Sensor s,int accuracy) {}
    @Override public void onDestroy() {if(manager!=null)manager.unregisterListener(this);super.onDestroy();}
    @Override public IBinder onBind(Intent intent) {return null;}
}
