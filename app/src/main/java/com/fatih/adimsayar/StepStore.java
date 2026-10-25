package com.fatih.adimsayar;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

public final class StepStore {
    private static final String PREF = "adimplus_v1";
    private StepStore() {}
    public static SharedPreferences prefs(Context c) { return c.getSharedPreferences(PREF, Context.MODE_PRIVATE); }
    public static String day(int offset) {
        Calendar cal = Calendar.getInstance();
        cal.add(Calendar.DAY_OF_YEAR, offset);
        return new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(cal.getTime());
    }
    public static String hour() { return new SimpleDateFormat("HH", Locale.US).format(new Date()); }
    public static int steps(Context c,int offset) { return prefs(c).getInt("d_"+day(offset),0); }
    public static int hourSteps(Context c,int hour) { return prefs(c).getInt("h_"+day(0)+"_"+String.format(Locale.US,"%02d",hour),0); }
    public static int goal(Context c) { return prefs(c).getInt("goal",8000); }
    public static int height(Context c) { return prefs(c).getInt("height",175); }
    public static int weight(Context c) { return prefs(c).getInt("weight",75); }
    public static int stride(Context c) {
        int v=prefs(c).getInt("stride",0);
        return v>0?v:Math.round(height(c)*0.415f);
    }
    public static double distanceKm(Context c,int steps) { return steps * stride(c) / 100000.0; }
    public static int calories(Context c,int steps) { return Math.round(steps*0.04f * weight(c)/70f); }
    public static int activeMinutes(int steps) { return Math.round(steps/100f); } // Tahmini, sensorden dogrudan olculmez.
    public static int weekSteps(Context c) { int n=0;for(int i=0;i<7;i++) n+=steps(c,-i);return n; }
    public static int monthSteps(Context c) { int n=0;for(int i=0;i<30;i++) n+=steps(c,-i);return n; }
    public static int record(Context c) { int n=0;for(int i=0;i<365;i++)n=Math.max(n,steps(c,-i));return n; }
    public static int goalDays(Context c) { int n=0;for(int i=0;i<30;i++)if(steps(c,-i)>=goal(c))n++;return n; }
    public static int streak(Context c) {
        int s=0,i=0;
        if(steps(c,0)<goal(c))i=1;
        for(;i<365;i++){if(steps(c,-i)>=goal(c))s++;else break;}
        return s;
    }
    public static synchronized void add(Context c,int n) {
        if(n<=0)return;
        if(n>20000)n=20000;
        SharedPreferences p=prefs(c);
        String d=day(0),h=hour();
        p.edit().putInt("d_"+d,p.getInt("d_"+d,0)+n)
                .putInt("h_"+d+"_"+h,p.getInt("h_"+d+"_"+h,0)+n)
                .putLong("lifetime",p.getLong("lifetime",0)+n).apply();
    }
    public static synchronized int ingestCounter(Context c,float raw) {
        SharedPreferences p=prefs(c);
        long boot=System.currentTimeMillis()-SystemClock.elapsedRealtime();
        long oldBoot=p.getLong("boot",0);
        float previous=p.getFloat("raw",-1f);
        int delta=0;
        if(oldBoot!=0 && Math.abs(boot-oldBoot)<300000 && previous>=0 && raw>=previous)
            delta=Math.min(20000,(int)(raw-previous));
        // İlk okumada veya yeniden baslamada mevcut toplamı ekleme: cift sayimi engeller.
        p.edit().putLong("boot",boot).putFloat("raw",raw).apply();
        if(delta>0)add(c,delta);
        return delta;
    }
    public static void clearHistory(Context c) {
        SharedPreferences p=prefs(c);SharedPreferences.Editor e=p.edit();
        for(String k:p.getAll().keySet()) if(k.startsWith("d_")||k.startsWith("h_")||k.equals("lifetime"))e.remove(k);
        e.apply();
    }
}
