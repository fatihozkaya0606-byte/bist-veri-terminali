package com.fatih.adimsayar;

import android.graphics.*;
import android.view.*;
import android.content.*;
import java.text.*;
import java.util.*;

/** Native, interactive rendering of the approved ADIM+ neon night dashboard.
 * Every metric is read from StepStore / WellnessStore; this is NOT a static screenshot.
 */
public final class NeonDashboard extends View {
    private static final int MINT=Color.rgb(63,248,226),WHITE=0xFFF3FCFF,SOFT=0xFF9FC0D9,BLUE=0xFF59C7FF;
    private final MainActivity host;
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG);
    private final Locale tr=new Locale("tr","TR");
    private float scale=1f;
    private final Random stars=new Random(14);
    public NeonDashboard(MainActivity activity) {
        super(activity);host=activity;setLayerType(View.LAYER_TYPE_SOFTWARE,null);
        setContentDescription("Adım+ canlı günlük adım panosu");
    }
    private static int color(String c){return Color.parseColor(c);}
    @Override protected void onMeasure(int widthSpec,int heightSpec){
        int w=MeasureSpec.getSize(widthSpec);
        float density=getResources().getDisplayMetrics().density;
        int h=(int)(700f*(w/(390f*density))*density+0.5f);
        setMeasuredDimension(w,h);
    }
    private void reset(){p.reset();p.setAntiAlias(true);p.setFilterBitmap(true);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);}
    private void text(Canvas c,String value,float x,float baseline,float sp,int col,boolean bold){
        reset();p.setTypeface(bold?Typeface.create("sans-serif",Typeface.BOLD):Typeface.create("sans-serif",Typeface.NORMAL));
        p.setTextSize(sp);p.setColor(col);c.drawText(value,x,baseline,p);
    }
    private void center(Canvas c,String value,float x,float baseline,float sp,int col,boolean bold){
        reset();p.setTextAlign(Paint.Align.CENTER);
        p.setTypeface(Typeface.create("sans-serif",bold?Typeface.BOLD:Typeface.NORMAL));
        p.setTextSize(sp);p.setColor(col);c.drawText(value,x,baseline,p);
    }
    private void rounded(Canvas c,float left,float top,float right,float bottom,float radius,int fill,int outline){
        reset();p.setColor(fill);p.setStyle(Paint.Style.FILL);
        c.drawRoundRect(left,top,right,bottom,radius,radius,p);
        if(outline!=0){p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(.75f);p.setColor(outline);c.drawRoundRect(left+.4f,top+.4f,right-.4f,bottom-.4f,radius,radius,p);}
        p.setStyle(Paint.Style.FILL);
    }
    private void grad(Canvas c,float x,float y,float w,float h,float radius,int start,int end,int stroke){
        reset();p.setShader(new LinearGradient(x,y,x+w,y+h,start,end,Shader.TileMode.CLAMP));
        c.drawRoundRect(x,y,x+w,y+h,radius,radius,p);p.setShader(null);
        if(stroke!=0){p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(.75f);p.setColor(stroke);c.drawRoundRect(x+.4f,y+.4f,x+w-.4f,y+h-.4f,radius,radius,p);p.setStyle(Paint.Style.FILL);}
    }
    private void stroke(Canvas c,float x,float y,float x2,float y2,float width,int col){
        reset();p.setColor(col);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(width);c.drawLine(x,y,x2,y2,p);p.setStyle(Paint.Style.FILL);
    }
    private String fmt(int n){return NumberFormat.getIntegerInstance(tr).format(n);}
    private String km(double x){return String.format(tr,"%.2f",x);}
    @Override protected void onDraw(Canvas raw){
        super.onDraw(raw);
        float w=getWidth();scale=w/390f;
        raw.save();raw.scale(scale,scale);
        Canvas c=raw;
        reset();p.setColor(0xFF061725);c.drawColor(0xFF061725);
        background(c);
        int steps=StepStore.steps(host,0),goal=Math.max(1,StepStore.goal(host));
        int calories=StepStore.calories(host,steps);
        int minutes=StepStore.activeMinutes(steps);
        long all=StepStore.prefs(host).getLong("lifetime",0);
        int water=WellnessStore.water(host,0),waterGoal=WellnessStore.waterTarget(host);

        text(c,"Bugünkü hareketin",18,27,23,WHITE,true);
        text(c,new SimpleDateFormat("EEEE, d MMMM yyyy",tr).format(new Date()),18,50,12.5f,SOFT,false);
        ring(c,steps,goal);
        status(c);
        center(c,steps>=goal?"HEDEF TAMAMLANDI  ✦":"HEDEFE "+fmt(Math.max(0,goal-steps))+" ADIM KALDI",
               195,256,13.3f,MINT,true);
        rounded(c,76,266,314,271,3,0xFF18364D,0xFF36668B);
        float fill=(Math.min(1f,steps/(float)goal)*238f);
        if(fill>0.8f)grad(c,76,266,fill,5,2.5f,0xFF4EEADF,0xFF37B7FA,0);
        center(c,fmt(steps)+" / "+fmt(goal)+" adım     •     %"+String.format(tr,"%.1f",steps*100f/goal),
               195,289,11.3f,SOFT,false);
        card(c,12,298,118,75,0, "MESAFE",km(StepStore.distanceKm(host,steps))+" km","Bugün","↗");
        card(c,136,298,118,75,1, "KALORİ",fmt(calories)+" kcal","Bugün","♨");
        int bpm=StepStore.prefs(host).getInt("hr_bpm",0);
        String pulse=bpm>0?bpm+" BPM":"ÖLÇ";
        String pulseSubtitle=bpm>0?"Son ölçüm":"Dokun ve ölç";
        card(c,260,298,118,75,2,"KALP ATIM HIZI",pulse,pulseSubtitle,"♥");
        card(c,12,380,180,75,3,"AKTİF SÜRE","~"+fmt(minutes)+" dk","Bugün","◷");
        card(c,198,380,180,75,0,"TOPLAM ADIM",fmt((int)Math.min(Integer.MAX_VALUE,all)),"Bugün","◉");
        pace(c,12,462,215,74,StepStore.pace(host));
        water(c,234,462,144,74,water,waterGoal);
        chart(c,12,543,366,105);
        grad(c,12,656,366,39,19,0xFF0C2942,0xFF0A1C31,0xFF1B6E93);
        text(c,"◎",24,682,26,MINT,true);
        text(c,"Günlük hedefin için "+fmt(Math.max(0,goal-steps))+" adım daha at!",51,680,12,WHITE,false);
        text(c,"›",362,683,25,WHITE,false);
        raw.restore();
    }
    private void background(Canvas c){
        reset();p.setShader(new LinearGradient(0,52,0,280,0xFF06233A,0xFF08334D,Shader.TileMode.CLAMP));
        c.drawRect(0,56,390,296,p);p.setShader(null);
        c.save();c.clipRect(0,56,390,294);
        // Stars stay stationary while actual step values update.
        stars.setSeed(121);
        for(int i=0;i<44;i++){
            float x=stars.nextInt(390),y=62+stars.nextInt(140);
            p.setColor(i%4==0?0xAA63DDFA:0x587EE7EC);p.setStyle(Paint.Style.FILL);
            c.drawCircle(x,y,i%5==0?.8f:.45f,p);
        }
        reset();p.setColor(0xFF123E57);
        Path mountain=new Path();mountain.moveTo(0,182);
        mountain.cubicTo(20,152,41,154,72,178);
        mountain.cubicTo(113,150,135,163,159,184);
        mountain.cubicTo(187,158,211,154,248,180);
        mountain.cubicTo(288,154,314,167,346,173);
        mountain.cubicTo(365,151,382,156,400,181);
        mountain.lineTo(400,297);mountain.lineTo(0,297);mountain.close();c.drawPath(mountain,p);
        p.setColor(0xFF082D43);
        Path mountain2=new Path();mountain2.moveTo(0,217);
        mountain2.cubicTo(45,178,90,198,121,218);
        mountain2.cubicTo(172,188,220,186,266,208);
        mountain2.cubicTo(306,186,352,188,390,206);
        mountain2.lineTo(390,300);mountain2.lineTo(0,300);mountain2.close();c.drawPath(mountain2,p);
        p.setColor(0xFF073043);
        Path hill=new Path();hill.moveTo(0,215);
        hill.cubicTo(52,210,76,250,133,231);
        hill.cubicTo(191,209,245,233,284,210);
        hill.cubicTo(341,202,367,218,390,215);
        hill.lineTo(390,300);hill.lineTo(0,300);hill.close();c.drawPath(hill,p);
        Path road=new Path();road.moveTo(-12,229);
        road.cubicTo(62,249,127,274,215,245);
        road.cubicTo(285,219,338,222,397,218);
        reset();p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1.3f);p.setColor(0xFF14C8AE);
        p.setShadowLayer(5,0,0,0xFF18E7DC);c.drawPath(road,p);
        p.clearShadowLayer();p.setStyle(Paint.Style.FILL);
        // Distant walker, drawn rather than using a fixed app screenshot.
        stroke(c,345,185,350,200,3.2f,0xFF04121F);
        stroke(c,349,199,343,211,3.6f,0xFF04121F);
        stroke(c,349,199,355,210,3.2f,0xFF04121F);
        stroke(c,350,190,344,195,2.4f,0xFF04121F);
        stroke(c,350,190,356,194,2.4f,0xFF04121F);
        reset();p.setColor(0xFF04121F);c.drawCircle(348,179,4,p);
        reset();p.setShader(new LinearGradient(0,225,0,298,0x00061725,0xFF061725,Shader.TileMode.CLAMP));c.drawRect(0,220,390,298,p);p.setShader(null);
        c.restore();
    }
    private void ring(Canvas c,int steps,int goal){
        float x=193,y=164,r=74;
        reset();p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(16);
        p.setColor(0xFF234A61);c.drawCircle(x,y,r,p);
        p.setStrokeWidth(1.5f);p.setColor(0xFF25DFD6);
        p.setShadowLayer(15,0,0,0xFF31E7EE);c.drawCircle(x,y,r+10,p);p.clearShadowLayer();
        reset();p.setColor(0xFF081C2E);c.drawCircle(x,y,r-10,p);
        reset();p.setStyle(Paint.Style.STROKE);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeWidth(13);
        p.setColor(MINT);p.setShadowLayer(8,0,0,MINT);
        float sweep=Math.max(0.6f,Math.min(360f,steps*360f/Math.max(1,goal)));
        c.drawArc(x-r,y-r,x+r,y+r,-90,sweep,false,p);p.clearShadowLayer();
        center(c,"BUGÜN",x,y-17,12.5f,SOFT,true);
        center(c,fmt(steps),x,y+22,40,WHITE,true);
        center(c,"ADIM",x,y+43,14,MINT,true);
    }
    private void status(Canvas c){
        grad(c,270,90,111,39,19,0xFF034239,0xFF041D2E,0xFF1DFCCB);
        reset();p.setColor(0xFF3BF976);p.setShadowLayer(5,0,0,0xFF1AFF84);
        c.drawCircle(283,108,4.3f,p);p.clearShadowLayer();
        text(c,"♙",292,117,19,MINT,true);
        text(c,StepService.isWalkingMode?"HIZLI":"YÜRÜYÜŞ",314,105,9,MINT,true);
        text(c,StepService.isWalkingMode?"YÜRÜYÜŞ":"TAKİBİ",314,116,9,MINT,true);
        text(c,StepService.isWalkingMode?"DURDUR":StepService.isSensorReady?"AKTİF":"BEKLEME",314,127,9,StepService.isSensorReady?MINT:0xFFFFC47A,true);
    }
    private void badge(Canvas c,float x,float y,int type,String glyph){
        int[] highlights={0xFF127AEF,0xFFF26A33,0xFFFF3772,0xFF04A78C};
        int co=highlights[type];
        grad(c,x,y,28,28,14,co,0xFF0E3857,co);
        center(c,glyph,x+14,y+19,17,WHITE,true);
    }
    private void card(Canvas c,float x,float y,float width,float height,int kind,String title,String value,String sub,String glyph){
        int[] shades={0xFF063A6E,0xFF352233,0xFF401E38,0xFF06483F};
        int[] darks={0xFF0A1D34,0xFF171A2A,0xFF23152B,0xFF062833};
        int[] strokes={0xFF1888DE,0xFF8F445A,0xFFAC466D,0xFF08CCB1};
        grad(c,x,y,width,height,14,shades[kind],darks[kind],strokes[kind]);
        c.save();c.clipRect(x+1,y+1,x+width-1,y+height-1);
        reset();p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(.8f);p.setColor(strokes[kind]);
        Path wave=new Path();wave.moveTo(x-5,y+62);
        wave.cubicTo(x+width*.34f,y+46,x+width*.48f,y+87,x+width+8,y+47);
        c.drawPath(wave,p);p.setStyle(Paint.Style.FILL);
        c.restore();
        badge(c,x+8,y+10,kind,glyph);
        float tx=x+42;
        text(c,title,tx,y+20,title.length()>11?8.6f:10.5f,SOFT,true);
        text(c,value,tx,y+45,width<125?17:21,WHITE,true);
        text(c,sub,tx,y+62,10.5f,SOFT,false);
        if(kind==2){
            Path ecg=new Path();ecg.moveTo(x+width-47,y+67);ecg.lineTo(x+width-35,y+67);ecg.lineTo(x+width-30,y+53);ecg.lineTo(x+width-26,y+70);ecg.lineTo(x+width-19,y+40);ecg.lineTo(x+width-13,y+61);ecg.lineTo(x+width-5,y+58);
            reset();p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1.5f);p.setColor(0xFFFF557D);c.drawPath(ecg,p);p.setStyle(Paint.Style.FILL);
        }
        if(title.equals("TOPLAM ADIM"))for(int i=0;i<5;i++)rounded(c,x+width-50+i*8,y+height-10-(i*4),x+width-45+i*8,y+height-9,1,0xFF397BB5,0);
    }
    private void pace(Canvas c,float x,float y,float width,float h,int pace){
        grad(c,x,y,width,h,14,0xFF0B3A5A,0xFF0B1E33,0xFF137AB5);
        badge(c,x+9,y+10,0,"◉");
        text(c,"YÜRÜYÜŞ TEMPOSU",x+43,y+21,10,SOFT,true);
        text(c,fmt(pace)+" adım/dk",x+43,y+45,19,MINT,true);
        text(c,"Son 5 dakika: "+(pace>90?"Tempolu":pace>15?"Yürüyüş":"Hareketsiz"),x+43,y+62,10,SOFT,false);
        for(int i=0;i<5;i++){
            int n=StepStore.minuteSteps(host,-4+i);
            float hBar=6+Math.min(22,n/4f);
            rounded(c,x+width-54+i*8,y+60-hBar,x+width-49+i*8,y+60,2,0xFF2D6795,0);
        }
    }
    private void water(Canvas c,float x,float y,float w,float h,int n,int g){
        grad(c,x,y,w,h,14,0xFF063F5C,0xFF061C31,0xFF139CD0);
        badge(c,x+8,y+9,0,"♢");
        text(c,"SU TAKİBİ",x+44,y+20,10,SOFT,true);
        text(c,String.format(tr,"%.1f / %.1f L",n/1000f,g/1000f),x+43,y+44,17,WHITE,true);
        rounded(c,x+w-35,y+10,x+w-7,y+38,14,0xFF045071,0xFF168FE0);
        center(c,"+",x+w-21,y+31,23,MINT,true);
        for(int i=0;i<4;i++){
            float xx=x+47+i*23;
            float ratio=Math.min(1f,n/(float)Math.max(1,g));
            boolean full=ratio>(i/4f);
            reset();p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1.5f);p.setColor(full?0xFF7EEAFF:0xFF447492);
            Path cup=new Path();cup.moveTo(xx,y+54);cup.lineTo(xx+12,y+54);cup.lineTo(xx+10,y+65);cup.lineTo(xx+2,y+65);cup.close();c.drawPath(cup,p);p.setStyle(Paint.Style.FILL);
        }
    }
    private void chart(Canvas c,float x,float y,float width,float h){
        grad(c,x,y,width,h,17,0xFF082942,0xFF081B2E,0xFF1D6B9A);
        text(c,"▥",x+13,y+23,20,BLUE,true);
        text(c,"SAATLİK HAREKET GRAFİĞİ",x+39,y+22,11,SOFT,true);
        text(c,"Bugün  ›",x+width-64,y+21,11,SOFT,false);
        float top=y+38,base=y+86,left=x+36,right=x+width-10;
        for(int j=0;j<3;j++){
            float yy=top+(base-top)*j/2f;
            stroke(c,left,yy,right,yy,.4f,0xFF315770);
            text(c,j==0?"2.000":j==1?"1.000":"0",x+8,yy+4,9,SOFT,false);
        }
        int max=1;for(int i=0;i<24;i++)max=Math.max(max,StepStore.hourSteps(host,i));
        for(int i=0;i<24;i++){
            float xx=left+(right-left)*i/23f;
            int n=StepStore.hourSteps(host,i);
            float yy=base-(base-top)*(n/(float)max);
            if(n>0)stroke(c,xx,base,xx,yy,2.5f,0xFF38CAFF);
            reset();p.setColor(0xFF5DEAFF);c.drawCircle(xx,yy,1.35f,p);
            if(i%3==0)text(c,String.format(tr,"%02d",i),xx-7,y+99,8.8f,SOFT,false);
        }
    }
    @Override public boolean onTouchEvent(android.view.MotionEvent event){
        if(event.getAction()!=MotionEvent.ACTION_UP)return true;
        float x=event.getX()/scale,y=event.getY()/scale;
        if(y>298&&y<373){
            if(x>259)host.openHeartRate();
            else if(x>130)host.openTab(1);
            else host.openTab(1);
        }else if(y>380&&y<455){
            host.openTab(1);
        }else if(y>462&&y<536){
            if(x>232){WellnessStore.addWater(host,250);invalidate();}
            else host.openTab(1);
        }else if(y>543&&y<650){
            host.openTab(1);
        }else if(y>655){
            host.openTab(3);
        }else if(y>85&&y<135&&x>267){
            host.toggleWalkMode();
        }
        return true;
    }
}
