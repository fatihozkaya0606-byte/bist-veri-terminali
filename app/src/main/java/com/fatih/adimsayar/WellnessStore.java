package com.fatih.adimsayar;
import android.content.Context;
import android.content.SharedPreferences;

public final class WellnessStore {
    private WellnessStore(){}
    public static int water(Context c,int offset) {
        return StepStore.prefs(c).getInt("water_"+StepStore.day(offset),0);
    }
    public static int waterTarget(Context c) {return StepStore.prefs(c).getInt("water_target",2000);}
    public static void addWater(Context c,int ml) {
        SharedPreferences p=StepStore.prefs(c);
        p.edit().putInt("water_"+StepStore.day(0),Math.max(0,Math.min(10000,water(c,0)+ml))).apply();
    }
    public static float weight(Context c,int offset) {
        return StepStore.prefs(c).getFloat("bodyweight_"+StepStore.day(offset),0);
    }
    public static float currentWeight(Context c){
        for(int d=0;d>=-365;d--){
            float w=weight(c,d);
            if(w>0)return w;
        }
        return StepStore.weight(c);
    }
    public static void saveWeight(Context c,float kg){
        if(kg>=30&&kg<=250){
            StepStore.prefs(c).edit().putFloat("bodyweight_"+StepStore.day(0),kg).putInt("weight",Math.round(kg)).apply();
        }
    }
    public static String note(Context c){return StepStore.prefs(c).getString("note_"+StepStore.day(0),"");}
    public static void note(Context c,String v){StepStore.prefs(c).edit().putString("note_"+StepStore.day(0),v.trim()).apply();}
    public static int last7Water(Context c) {int t=0;for(int d=0;d>-7;d--)t+=water(c,d);return t;}
}
