package com.fatih.adimsayar;

import android.app.*;
import android.os.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.SurfaceTexture;
import android.hardware.Camera;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.view.*;
import android.widget.*;
import java.util.*;
import java.text.*;

/**
 * Experimental camera-based optical pulse estimate. No BPM is shown until
 * a plausible repeating optical signal has actually been measured.
 * Not a medical device, and no diagnostic claims.
 */
@SuppressWarnings("deprecation")
public class HeartRateActivity extends Activity implements Camera.PreviewCallback {
    private Camera camera;
    private SurfaceTexture surface;
    private TextView count,subtitle,progress;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final ArrayList<Long> times=new ArrayList<>();
    private final ArrayList<Double> values=new ArrayList<>();
    private long start=0,lastFrame=0;
    private boolean measuring=false,finishing=false;
    private static final int PINK=0xFFFF648B;
    int dp(float n){return (int)(getResources().getDisplayMetrics().density*n+.5f);}
    GradientDrawable shape(int fill,int border,int radius) {
        GradientDrawable g=new GradientDrawable();g.setColor(fill);g.setCornerRadius(dp(radius));
        if(border!=0)g.setStroke(dp(1),border);return g;
    }
    TextView label(String v,int size,int color,boolean bold){
        TextView t=new TextView(this);t.setText(v);t.setTextSize(size);t.setTextColor(color);
        if(bold)t.setTypeface(android.graphics.Typeface.DEFAULT,1);t.setGravity(Gravity.CENTER);return t;
    }
    void append(LinearLayout box,View v,int margin){
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(margin);box.addView(v,lp);
    }
    @Override public void onCreate(Bundle b){
        super.onCreate(b);
        getWindow().setStatusBarColor(0xFF07121C);getWindow().setNavigationBarColor(0xFF07121C);
        LinearLayout root=new LinearLayout(this);root.setOrientation(1);root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(dp(24),dp(26),dp(24),dp(24));root.setBackgroundColor(0xFF07121C);
        setContentView(root);
        TextView title=label("♥  KALP ATIM HIZI",23,PINK,true);append(root,title,12);
        TextView instructions=label("Telefonun arka kamerasını ve flaşını parmağınla tamamen kapat. Hareket etmeden ölçüm yap.",14,0xFFA8BACD,false);
        append(root,instructions,20);
        count=label("—",58,Color.WHITE,true);append(root,count,28);
        subtitle=label("BPM • Tahmini kamera ölçümü",13,0xFFA8BACD,false);append(root,subtitle,0);
        progress=label("Parmağını kameraya yerleştir",14,PINK,true);append(root,progress,28);
        TextView startButton=label("ÖLÇÜMÜ BAŞLAT",15,0xFF072536,true);
        startButton.setPadding(dp(16),dp(18),dp(16),dp(18));startButton.setBackground(shape(PINK,0,16));
        append(root,startButton,30);startButton.setOnClickListener(v->startCamera());
        TextView back=label("Geri dön",14,0xFF9BB2C8,false);
        append(root,back,23);back.setOnClickListener(v->finish());
        TextView disclaimer=label("Sağlık amaçlı tanı aracı değildir. Telefon kamerası ölçümü hatalı olabilir. Rahatsızlık veya çarpıntı durumunda tıbbi değerlendirme gerekir.",12,0xFF8BA3B6,false);
        append(root,disclaimer,29);
    }
    private void startCamera(){
        if(measuring)return;
        if(checkSelfPermission(android.Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED){
            requestPermissions(new String[]{android.Manifest.permission.CAMERA},320);
            return;
        }
        try{
            camera=Camera.open();
            Camera.Parameters params=camera.getParameters();
            if(params.getSupportedFlashModes()!=null &&
                params.getSupportedFlashModes().contains(Camera.Parameters.FLASH_MODE_TORCH)){
                params.setFlashMode(Camera.Parameters.FLASH_MODE_TORCH);
            }
            params.setPreviewFormat(android.graphics.ImageFormat.NV21);
            camera.setParameters(params);
            surface=new SurfaceTexture(41);
            camera.setPreviewTexture(surface);
            camera.setPreviewCallback(this);
            camera.startPreview();
            measuring=true;finishing=false;start=SystemClock.elapsedRealtime();lastFrame=0L;
            times.clear();values.clear();
            count.setText("♥");
            progress.setText("Ölçüm başladı • 15 saniye sabit tut");
            handler.postDelayed(()->{
                if(measuring && !finishing){
                    int bpm=estimate();
                    if(bpm>0)success(bpm);
                    else failure("Net bir nabız sinyali okunamadı. Parmağını kamera ve flaş üzerine yerleştirip yeniden dene.");
                }
            },21000);
        }catch(Exception ex){
            failure("Bu cihazda kamera ile nabız ölçümü başlatılamadı.");
        }
    }
    @Override public void onPreviewFrame(byte[] data,Camera cam){
        if(!measuring||data==null)return;
        long now=SystemClock.elapsedRealtime();
        if(now-lastFrame<85L)return;
        lastFrame=now;
        try{
            Camera.Size size=cam.getParameters().getPreviewSize();
            int w=size.width,h=size.height,plane=w*h;
            if(data.length<plane)return;
            long sum=0;int points=0;
            for(int yy=h/3;yy<h*2/3;yy+=8)
                for(int xx=w/3;xx<w*2/3;xx+=8) {
                    sum+=data[yy*w+xx]&255;points++;
                }
            if(points==0)return;
            double average=sum/(double)points;
            times.add(now);values.add(average);
            long elapsed=now-start;
            if(values.size()%12==0){
                final int seconds=(int)(elapsed/1000L);
                runOnUiThread(()->progress.setText("Parmağını sabit tut • "+seconds+" / 20 sn"));
            }
            if(elapsed>12000 && values.size()>80 && values.size()%15==0){
                int bpm=estimate();
                if(bpm>0 && elapsed>14000)runOnUiThread(()->success(bpm));
            }
        }catch(Exception ignored){}
    }
    private synchronized int estimate(){
        int n=values.size();
        if(n<70)return 0;
        double sum=0,sumSquares=0;
        for(double x:values){sum+=x;sumSquares+=x*x;}
        double avg=sum/n,sd=Math.sqrt(Math.max(0,sumSquares/n-avg*avg));
        // No covered, well-lit finger or no fluctuating optical signal.
        if(avg<35 || avg>251 || sd<.11 || sd>35)return 0;
        ArrayList<Long> peaks=new ArrayList<>();
        for(int i=5;i<n-5;i++){
            long t=times.get(i);
            if(t<times.get(n-1)-13000L)continue;
            double center=0;int nb=0;
            for(int j=Math.max(0,i-7);j<=Math.min(n-1,i+7);j++){center+=values.get(j);nb++;}
            center/=nb;
            double at=values.get(i);
            if(at-center<Math.max(.12,sd*.18))continue;
            if(at<values.get(i-1)||at<=values.get(i+1))continue;
            if(!peaks.isEmpty()&&t-peaks.get(peaks.size()-1)<340L)continue;
            peaks.add(t);
        }
        if(peaks.size()<8)return 0;
        ArrayList<Long> intervals=new ArrayList<>();
        for(int i=1;i<peaks.size();i++){
            long dt=peaks.get(i)-peaks.get(i-1);
            if(dt>=340L&&dt<=1300L)intervals.add(dt);
        }
        if(intervals.size()<6)return 0;
        Collections.sort(intervals);
        long median=intervals.get(intervals.size()/2);
        int good=0;
        for(Long x:intervals)if(Math.abs(x-median)<median*.22)good++;
        if(good<Math.max(5,intervals.size()*2/3))return 0;
        int bpm=(int)Math.round(60000d/median);
        return bpm>=45&&bpm<=175?bpm:0;
    }
    private void success(int bpm){
        if(finishing)return;
        finishing=true;
        stopCamera();
        StepStore.prefs(this).edit()
            .putInt("hr_bpm",bpm)
            .putLong("hr_time",System.currentTimeMillis()).apply();
        count.setText(bpm+"");
        subtitle.setText("BPM • Tahmini nabız");
        progress.setText("Ölçüm tamamlandı. Sonuç kaydedildi.");
        Toast.makeText(this,"Tahmini nabız: "+bpm+" BPM",Toast.LENGTH_LONG).show();
    }
    private void failure(String reason){
        finishing=true;stopCamera();
        progress.setText(reason);
        count.setText("—");
        Toast.makeText(this,reason,Toast.LENGTH_LONG).show();
    }
    private void stopCamera(){
        measuring=false;
        if(camera!=null){
            try{camera.setPreviewCallback(null);camera.stopPreview();camera.release();}catch(Exception ignored){}
            camera=null;
        }
        if(surface!=null){try{surface.release();}catch(Exception ignored){}surface=null;}
    }
    @Override public void onRequestPermissionsResult(int code,String[] names,int[] grants){
        super.onRequestPermissionsResult(code,names,grants);
        if(code==320){
            if(grants.length>0&&grants[0]==PackageManager.PERMISSION_GRANTED)startCamera();
            else progress.setText("Kamera izni olmadan nabız ölçülemez.");
        }
    }
    @Override protected void onDestroy(){stopCamera();handler.removeCallbacksAndMessages(null);super.onDestroy();}
}
