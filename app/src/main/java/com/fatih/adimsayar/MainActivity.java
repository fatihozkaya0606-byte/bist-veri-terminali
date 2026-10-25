package com.fatih.adimsayar;

import android.Manifest;
import android.app.*;
import android.animation.ValueAnimator;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.*;
import android.graphics.drawable.*;
import android.hardware.*;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import java.text.*;
import java.util.*;

public class MainActivity extends Activity {
    private static final int MINT=0xFF58F5DB;
    private static final int BLUE=0xFF60B8FF;
    private boolean dark=true;
    private int bg,card,fg,muted,border,smallCard;
    private int page=0;
    private Ring liveRing;
    private TextView liveGoal,livePercent,liveDistance,liveCalories,liveMinutes,liveLifetime;
    private ProgressBar liveProgress;
    private TextView livePace,liveActivity;
    private Bars liveBars;
    private boolean receiverRegistered=false;
    private final BroadcastReceiver stepReceiver=new BroadcastReceiver(){
        @Override public void onReceive(Context c,Intent intent){updateLivePanels();}
    };
    private LinearLayout root,content,nav;
    private ScrollView scroll;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private int displayed=-1;
    private final Runnable tick=new Runnable(){public void run(){
        if(page==0)updateLivePanels();
        handler.postDelayed(this,650);
    }};
    int dp(float f){return (int)(getResources().getDisplayMetrics().density*f+0.5f);}
    private final Locale TR=new Locale("tr","TR");
    @Override public void onCreate(Bundle b){super.onCreate(b);dark=StepStore.prefs(this).getBoolean("dark",true);render();beginTracking();}
    @Override protected void onResume(){
        super.onResume();
        IntentFilter filter=new IntentFilter(StepService.UPDATE_ACTION);
        if(Build.VERSION.SDK_INT>=33)registerReceiver(stepReceiver,filter,Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(stepReceiver,filter);
        receiverRegistered=true;
        handler.removeCallbacks(tick);
        handler.post(tick);
    }
    @Override protected void onPause(){
        handler.removeCallbacks(tick);
        if(receiverRegistered){unregisterReceiver(stepReceiver);receiverRegistered=false;}
        super.onPause();
    }
    private void colors(){
        bg=dark?0xFF07121C:0xFFF4F8FB;card=dark?0xFF122433:0xFFFFFFFF;
        smallCard=dark?0xFF192E3E:0xFFF0F5F8;
        fg=dark?0xFFF1FCFF:0xFF102836;muted=dark?0xFF92ADBD:0xFF607A89;border=dark?0xFF294456:0xFFDDE8EE;
        getWindow().setStatusBarColor(bg);getWindow().setNavigationBarColor(bg);
        getWindow().getDecorView().setSystemUiVisibility(dark?0:View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
    }
    GradientDrawable shape(int color,int r,int line){GradientDrawable d=new GradientDrawable();d.setColor(color);d.setCornerRadius(dp(r));if(line!=0)d.setStroke(dp(1),line);return d;}
    TextView txt(String text,int size,boolean bold,int color){TextView t=new TextView(this);t.setText(text);t.setTextSize(size);t.setTextColor(color);t.setFontFeatureSettings("tnum");if(bold)t.setTypeface(android.graphics.Typeface.DEFAULT,1);return t;}
    void add(LinearLayout parent,View child,int mt){LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.topMargin=dp(mt);parent.addView(child,p);}
    LinearLayout box(int pad){LinearLayout v=new LinearLayout(this);v.setOrientation(1);v.setPadding(dp(pad),dp(pad),dp(pad),dp(pad));v.setBackground(shape(card,22,border));return v;}
    LinearLayout line(){LinearLayout v=new LinearLayout(this);v.setOrientation(LinearLayout.HORIZONTAL);v.setGravity(Gravity.CENTER_VERTICAL);return v;}
    String fmt(int n){return NumberFormat.getIntegerInstance(TR).format(n);}
    String km(double n){return String.format(TR,"%.2f",n);}
    void label(LinearLayout in,String heading,String caption){in.addView(txt(heading,17,true,fg));TextView t=txt(caption,12,false,muted);add(in,t,4);}
    void render(){
        colors(); displayed=StepStore.steps(this,0);
        liveRing=null;liveGoal=null;livePercent=null;liveDistance=null;
        liveCalories=null;liveMinutes=null;liveLifetime=null;livePace=null;liveActivity=null;liveProgress=null;liveBars=null;
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(bg);
        setContentView(root);
        LinearLayout head=line();head.setPadding(dp(22),dp(8),dp(22),dp(5));
        TextView logo=txt("ADIM+   ✦",23,true,MINT);
        head.addView(logo,new LinearLayout.LayoutParams(0,-2,1));
        TextView ver=txt("●  TEST 1.2",11,true,muted);head.addView(ver);
        root.addView(head);
        scroll=new ScrollView(this);scroll.setFillViewport(false);scroll.setVerticalScrollBarEnabled(false);
        content=new LinearLayout(this);content.setPadding(dp(16),dp(2),dp(16),dp(22));content.setOrientation(1);scroll.addView(content);
        root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        if(page==0)today();else if(page==1)analysis();else if(page==2)history();else settings();
        nav=line();nav.setPadding(dp(8),dp(5),dp(8),dp(7));nav.setBackground(shape(card,18,0));
        String[] icons={"◉","▥","▤","⚙"};String[] titles={"Bugün","Analiz","Geçmiş","Ayarlar"};
        for(int i=0;i<4;i++){
            final int tab=i;LinearLayout cell=new LinearLayout(this);cell.setOrientation(1);cell.setGravity(Gravity.CENTER);
            cell.setPadding(dp(2),dp(7),dp(2),dp(5));cell.setBackground(shape(i==page?(dark?0xFF234D52:0xFFDBF7F1):card,16,0));
            TextView icon=txt(icons[i],21,true,i==page?MINT:muted);icon.setGravity(Gravity.CENTER);cell.addView(icon);
            TextView title=txt(titles[i],11,i==page,i==page?fg:muted);title.setGravity(Gravity.CENTER);cell.addView(title);
            nav.addView(cell,new LinearLayout.LayoutParams(0,dp(55),1));cell.setOnClickListener(v->{page=tab;render();});
        }
        root.addView(nav);
    }
    private void headline(String title,String subtitle){add(content,txt(title,24,true,fg),8);add(content,txt(subtitle,13,false,muted),5);}
    private void today(){
        String date=new SimpleDateFormat("EEEE, d MMMM yyyy",TR).format(new Date());
        headline("Bugünkü hareketin",date);
        int steps=StepStore.steps(this,0),goal=StepStore.goal(this);
        LinearLayout hero=box(15);
        hero.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
            new int[]{dark?0xFF19374B:0xFFE7FCF7,card}));
        add(content,hero,12);
        liveRing=new Ring(steps,goal);
        hero.addView(liveRing,new LinearLayout.LayoutParams(-1,dp(188)));
        liveGoal=txt("",13,true,MINT);liveGoal.setGravity(Gravity.CENTER);hero.addView(liveGoal);
        livePercent=txt("",12,false,muted);livePercent.setGravity(Gravity.CENTER);add(hero,livePercent,6);
        liveProgress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
        liveProgress.setMax(Math.max(1,goal));
        liveProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(MINT));
        liveProgress.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(border));
        add(hero,liveProgress,13);
        LinearLayout status=line();status.setPadding(dp(13),dp(10),dp(13),dp(10));
        status.setBackground(shape(dark?0xFF163B3F:0xFFDDF6EF,12,0));
        add(content,status,11);
        SensorManager sm=(SensorManager)getSystemService(SENSOR_SERVICE);
        boolean has=sm.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)!=null||sm.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)!=null;
        boolean perm=Build.VERSION.SDK_INT<29||checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)==PackageManager.PERMISSION_GRANTED;
        TextView info=txt(!has?"⚠ Bu telefonda adım sensörü yok":!perm?"⚠ Hareket izni vermek için dokun":"●  CANLI TAKİP AKTİF  •  Fiziksel adım sensörü",12,true,has&&perm?MINT:0xFFFFB86B);
        status.addView(info);
        status.setOnClickListener(v->beginTracking());
        LinearLayout row=line();add(content,row,10);
        liveDistance=stat(row,"MESAFE",km(StepStore.distanceKm(this,steps))+" km","↗",BLUE);
        liveCalories=stat(row,"KALORİ",fmt(StepStore.calories(this,steps))+" kcal","♨",MINT);
        row=line();add(content,row,8);
        liveMinutes=stat(row,"AKTİF SÜRE*","~"+fmt(StepStore.activeMinutes(steps))+" dk","◷",MINT);
        liveLifetime=stat(row,"TOPLAM ADIM",fmt((int)Math.min(Integer.MAX_VALUE,StepStore.prefs(this).getLong("lifetime",0))),"⌁",BLUE);
        LinearLayout tempoPanel=box(13);add(content,tempoPanel,10);
        LinearLayout tempoLine=line();
        LinearLayout tempoLeft=new LinearLayout(this);tempoLeft.setOrientation(1);
        tempoLeft.addView(txt("YÜRÜYÜŞ TEMPOSU",12,true,muted));
        livePace=txt("0 adım/dk",20,true,MINT);add(tempoLeft,livePace,6);
        tempoLine.addView(tempoLeft,new LinearLayout.LayoutParams(0,-2,1));
        LinearLayout tempoRight=new LinearLayout(this);tempoRight.setOrientation(1);
        TextView paceLbl=txt("SON 5 DAKİKA",11,true,muted);paceLbl.setGravity(Gravity.RIGHT);tempoRight.addView(paceLbl);
        liveActivity=txt("Sensör ölçümü",12,false,fg);liveActivity.setGravity(Gravity.RIGHT);add(tempoRight,liveActivity,8);
        tempoLine.addView(tempoRight);tempoPanel.addView(tempoLine);
        LinearLayout waterCard=box(14);add(content,waterCard,12);
        LinearLayout wr=line();
        LinearLayout wl=new LinearLayout(this);wl.setOrientation(1);
        wl.addView(txt("SU TAKİBİ   💧",16,true,fg));
        add(wl,txt(WaterText()+"  •  Hedef "+WellnessStore.waterTarget(this)+" ml",12,false,muted),5);
        wr.addView(wl,new LinearLayout.LayoutParams(0,-2,1));
        TextView minus=chip("−");TextView plus=chip("+");
        wr.addView(minus);wr.addView(plus);waterCard.addView(wr);
        minus.setOnClickListener(v->{WellnessStore.addWater(this,-250);rerenderStay();});
        plus.setOnClickListener(v->{WellnessStore.addWater(this,250);rerenderStay();});
        ProgressBar waterBar=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
        waterBar.setMax(WellnessStore.waterTarget(this));waterBar.setProgress(WellnessStore.water(this,0));
        waterBar.setProgressTintList(android.content.res.ColorStateList.valueOf(BLUE));
        waterBar.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(border));
        add(waterCard,waterBar,11);
        LinearLayout quick=line();add(content,quick,10);
        TextView weightBtn=tile("⚖  KİLO",String.format(TR,"%.1f kg",WellnessStore.currentWeight(this)));
        TextView notesBtn=tile("✎  GÜNLÜK NOT",WellnessStore.note(this).isEmpty()?"Bir not ekle":"Not kayıtlı ✓");
        LinearLayout.LayoutParams qp=new LinearLayout.LayoutParams(0,dp(78),1);qp.setMargins(0,0,dp(5),0);quick.addView(weightBtn,qp);
        LinearLayout.LayoutParams qp2=new LinearLayout.LayoutParams(0,dp(78),1);qp2.setMargins(dp(5),0,0,0);quick.addView(notesBtn,qp2);
        weightBtn.setOnClickListener(v->editWeight());
        notesBtn.setOnClickListener(v->editNote());
        LinearLayout chart=box(14);add(content,chart,12);
        label(chart,"Saatlik hareket","Güncel saatlik adım dağılımı");
        liveBars=new Bars(hourData(),24);
        chart.addView(liveBars,new LinearLayout.LayoutParams(-1,dp(143)));
        LinearLayout motivate=box(13);add(content,motivate,10);
        motivate.addView(txt(steps>=goal?"✦  Günlük hedef tamamlandı!":steps>=goal/2?"✦  Yolu yarıladın, devam et!":"✦  Küçük adımlar büyük sonuçlar getirir",14,true,fg));
        add(motivate,txt("Mesafe, kalori ve aktif süre tahminidir. Ölçümler telefonunda saklanır. Sensörün iletim hızı cihazına göre değişebilir.",11,false,muted),7);
        action(content,"GÜNLÜK SONUCU PAYLAŞ",()->share());
        updateLivePanels();
    }
    private String WaterText(){return WellnessStore.water(this,0)+" ml";}
    private TextView chip(String symbol){
        TextView t=txt(symbol,23,true,fg);t.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(42),dp(39));lp.leftMargin=dp(7);
        t.setLayoutParams(lp);t.setBackground(shape(smallCard,12,0));return t;
    }
    private TextView tile(String a,String b){
        TextView t=txt(a+"\n"+b,13,true,fg);
        t.setGravity(Gravity.CENTER_VERTICAL);
        t.setPadding(dp(13),dp(9),dp(7),dp(9));
        t.setBackground(shape(card,17,border));return t;
    }
    private void rerenderStay(){
        final int y=scroll==null?0:scroll.getScrollY();
        render();if(scroll!=null)scroll.post(()->scroll.scrollTo(0,y));
    }
    private void updateLivePanels(){
        if(page!=0||liveRing==null)return;
        int steps=StepStore.steps(this,0);
        int pace=StepStore.pace(this);
        if(livePace!=null)livePace.setText(pace+" adım/dk");
        if(liveActivity!=null)liveActivity.setText(pace>100?"Tempolu yürüyüş":pace>=35?"Yürüyüş":pace>0?"Hafif hareket":"Hareketsiz");
        if(steps==displayed && livePercent!=null && livePercent.getText().length()>0)return;
        displayed=steps;int goal=StepStore.goal(this);
        liveRing.setSteps(steps);
        liveGoal.setText(steps>=goal?"✦  GÜNLÜK HEDEF TAMAMLANDI":"HEDEFE "+fmt(Math.max(0,goal-steps))+" ADIM KALDI");
        livePercent.setText(fmt(steps)+" / "+fmt(goal)+" adım    •    %"+
        (steps*100<goal*10?String.format(TR,"%.1f",steps*100f/Math.max(1,goal)):String.valueOf(steps*100/Math.max(1,goal))));
        liveProgress.setProgress(Math.min(goal,steps));
        liveDistance.setText(km(StepStore.distanceKm(this,steps))+" km");
        liveCalories.setText(fmt(StepStore.calories(this,steps))+" kcal");
        liveMinutes.setText("~"+fmt(StepStore.activeMinutes(steps))+" dk");
        liveLifetime.setText(fmt((int)Math.min(Integer.MAX_VALUE,StepStore.prefs(this).getLong("lifetime",0))));
        if(liveBars!=null)liveBars.setValues(hourData());
    }
    private void editWeight(){
        EditText input=new EditText(this);input.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_FLAG_DECIMAL);
        input.setText(String.format(java.util.Locale.US,"%.1f",WellnessStore.currentWeight(this)));
        new AlertDialog.Builder(this).setTitle("Bugünkü kilon").setMessage("30 ile 250 kg arasında değer yazabilirsin.")
            .setView(input).setNegativeButton("Vazgeç",null).setPositiveButton("Kaydet",(d,w)->{
                try{
                    float kg=Float.parseFloat(input.getText().toString().trim().replace(',','.'));
                    if(kg>=30&&kg<=250){WellnessStore.saveWeight(this,kg);rerenderStay();}
                    else Toast.makeText(this,"30–250 kg arasında gir",Toast.LENGTH_LONG).show();
                }catch(Exception e){Toast.makeText(this,"Geçerli kilo gir",Toast.LENGTH_LONG).show();}
            }).show();
    }
    private void editNote(){
        EditText input=new EditText(this);input.setSingleLine(false);input.setMinLines(3);
        input.setText(WellnessStore.note(this));input.setHint("Bugünkü yürüyüşün, hedefin veya notun...");
        new AlertDialog.Builder(this).setTitle("Günlük notun")
            .setView(input).setNegativeButton("Vazgeç",null).setPositiveButton("Kaydet",(d,w)->{
                WellnessStore.note(this,input.getText().toString());rerenderStay();
            }).show();
    }
    private TextView stat(LinearLayout row,String label,String value,String sym,int accent){
        LinearLayout panel=box(12);
        LinearLayout top=line();top.addView(txt(label,11,true,muted),new LinearLayout.LayoutParams(0,-2,1));
        top.addView(txt(sym,20,true,accent));panel.addView(top);
        TextView val=txt(value,21,true,fg);add(panel,val,8);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(0,dp(94),1);
        params.setMargins(dp(3),0,dp(3),0);row.addView(panel,params);
        return val;
    }
    int[] hourData(){int[] a=new int[24];for(int i=0;i<24;i++)a[i]=StepStore.hourSteps(this,i);return a;}
    void analysis(){
        headline("Hareket analizi","İlerlemeni gün gün takip et");
        int week=StepStore.weekSteps(this),month=StepStore.monthSteps(this);
        LinearLayout pair=line();add(content,pair,21);stat(pair,"SON 7 GÜN",fmt(week),"↗",MINT);stat(pair,"GÜNLÜK ORT.",fmt(week/7),"◷",BLUE);
        LinearLayout wk=box(16);add(content,wk,13);
        label(wk,"7 günlük adımlar","Günlere göre yürüyüş performansı");
        int[] a=new int[7];for(int i=0;i<7;i++)a[i]=StepStore.steps(this,i-6);
        wk.addView(new Bars(a,7),new LinearLayout.LayoutParams(-1,dp(195)));
        LinearLayout compare=box(16);add(content,compare,13);
        label(compare,"Haftalık karşılaştırma","Son 7 gün ve önceki 7 gün");
        int previousWeek=0;for(int i=7;i<14;i++)previousWeek+=StepStore.steps(this,-i);
        int diff=week-previousWeek;
        add(compare,txt((diff>=0?"↑ +":"↓ ")+fmt(diff)+" adım",22,true,diff>=0?MINT:0xFFFFAFAF),12);
        add(compare,txt("Önceki 7 gün: "+fmt(previousWeek)+" adım",12,false,muted),6);
        LinearLayout hydration=box(16);add(content,hydration,13);
        label(hydration,"Su tüketimi • Son 7 gün",String.format(TR,"%.1f litre toplam",WellnessStore.last7Water(this)/1000f));
        int[] waterWeek=new int[7];for(int i=0;i<7;i++)waterWeek[i]=WellnessStore.water(this,i-6);
        hydration.addView(new Bars(waterWeek,7),new LinearLayout.LayoutParams(-1,dp(125)));
        LinearLayout m=box(16);add(content,m,13);
        label(m,"30 günlük trend",fmt(month)+" adım toplam • "+fmt(month/30)+" adım/gün");
        int[] thirty=new int[30];for(int i=0;i<30;i++)thirty[i]=StepStore.steps(this,i-29);
        m.addView(new Trend(thirty),new LinearLayout.LayoutParams(-1,dp(160)));
        LinearLayout p=box(17);add(content,p,14);label(p,"Kişisel istatistikler","Kendi kayıtlarına göre");
        summary(p,"En yüksek gün",fmt(StepStore.record(this))+" adım");
        summary(p,"Hedef serisi",StepStore.streak(this)+" gün");
        summary(p,"Son 30 günde hedef",StepStore.goalDays(this)+" / 30 gün");
        summary(p,"30 günlük mesafe",km(StepStore.distanceKm(this,month))+" km");
        summary(p,"Güncel kilo",String.format(TR,"%.1f kg",WellnessStore.currentWeight(this)));
        summary(p,"Dünkü adımlar",fmt(StepStore.steps(this,-1))+" adım");
        summary(p,"En yoğun saat",StepStore.busiestHour(this));
        LinearLayout achievements=box(16);add(content,achievements,14);
        label(achievements,"Başarı rozetleri","Ulaştığın günlük adım kilometre taşları");
        int rec=StepStore.record(this);int[] limits={1000,5000,10000,20000};
        for(int n:limits){TextView b=txt((rec>=n?"✦   ":"○   ")+fmt(n)+" adım  "+(rec>=n?"• Kazanıldı":"• Kilitli"),14,rec>=n,rec>=n?MINT:muted);add(achievements,b,13);}
        add(content,txt("Not: Grafikler, sensör izin verildikten sonra toplanan kayıtları gösterir.",12,false,muted),13);
    }
    void history(){
        headline("Adım geçmişi","Son 30 günün ayrıntılı dökümü");
        LinearLayout box=box(16);add(content,box,20);
        for(int i=0;i<30;i++){
            int s=StepStore.steps(this,-i),g=StepStore.goal(this);String date;
            if(i==0)date="Bugün";else if(i==1)date="Dün";
            else {Calendar c=Calendar.getInstance();c.add(Calendar.DAY_OF_YEAR,-i);date=new SimpleDateFormat("d MMM, EEE",TR).format(c.getTime());}
            LinearLayout r=line();if(i>0) add(box,r,18);else box.addView(r);
            LinearLayout labels=new LinearLayout(this);labels.setOrientation(1);
            labels.addView(txt(date,14,true,fg));
            add(labels,txt(km(StepStore.distanceKm(this,s))+" km  •  "+StepStore.calories(this,s)+" kcal  •  💧"+WellnessStore.water(this,-i)+" ml",11,false,muted),4);
            r.addView(labels,new LinearLayout.LayoutParams(0,-2,1));
            r.addView(txt(fmt(s)+" "+(s>=g?"✓":""),15,true,s>=g?MINT:fg));
            ProgressBar pb=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
            pb.setMax(g);pb.setProgress(Math.min(g,s));pb.setProgressTintList(android.content.res.ColorStateList.valueOf(MINT));
            pb.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(border));
            add(box,pb,9);
        }
        add(content,txt("Geçmiş sadece uygulamanın sensörle topladığı adımları içerir; başka uygulamaların kayıtları otomatik aktarılmaz.",12,false,muted),15);
        action(content,"SON 90 GÜNÜ CSV OLARAK KAYDET",()->exportCsv());
    }
    private void exportCsv(){
        Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("text/csv");
        intent.putExtra(Intent.EXTRA_TITLE,"AdimPlus_son90gun.csv");
        startActivityForResult(intent,212);
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request!=212||result!=RESULT_OK||data==null||data.getData()==null)return;
        try(java.io.OutputStream output=getContentResolver().openOutputStream(data.getData())){
            if(output==null)throw new java.io.IOException("Dosya açılamadı");
            java.io.OutputStreamWriter writer=new java.io.OutputStreamWriter(output,"UTF-8");
            writer.write("Tarih,Adım,Mesafe_km,Tahmini_Kalori_kcal,Su_ml\n");
            for(int i=89;i>=0;i--){
                int count=StepStore.steps(this,-i);
                writer.write(StepStore.day(-i)+","+count+","+
                    String.format(Locale.US,"%.3f",StepStore.distanceKm(this,count))+","+
                    StepStore.calories(this,count)+","+WellnessStore.water(this,-i)+"\n");
            }
            writer.flush();Toast.makeText(this,"90 günlük rapor kaydedildi",Toast.LENGTH_LONG).show();
        }catch(Exception e){Toast.makeText(this,"Rapor kaydedilemedi: "+e.getMessage(),Toast.LENGTH_LONG).show();}
    }
    void settings(){
        headline("Kişisel ayarlar","Hedeflerini ve ölçümlerini düzenle");
        LinearLayout b=box(10);add(content,b,20);
        setting(b,"Günlük adım hedefi",fmt(StepStore.goal(this))+" adım","goal",2000,50000);
        setting(b,"Boy",StepStore.height(this)+" cm","height",100,230);
        setting(b,"Kilo",StepStore.weight(this)+" kg","weight",30,250);
        setting(b,"Adım uzunluğu",StepStore.stride(this)+" cm","stride",30,150);
        setting(b,"Günlük su hedefi",WellnessStore.waterTarget(this)+" ml","water_target",500,5000);
        LinearLayout theme=box(15);add(content,theme,15);
        LinearLayout t=line();t.addView(txt("Koyu tema",16,true,fg),new LinearLayout.LayoutParams(0,-2,1));
        Switch sw=new Switch(this);sw.setChecked(dark);t.addView(sw);theme.addView(t);
        add(theme,txt("Açık / koyu görünüm arasında geçiş yap",12,false,muted),7);
        sw.setOnCheckedChangeListener((button,isChecked)->{StepStore.prefs(this).edit().putBoolean("dark",isChecked).apply();dark=isChecked;render();});
        LinearLayout tips=box(16);add(content,tips,14);
        label(tips,"Nasıl ölçülüyor?","Verilerini doğru yorumla");
        add(tips,txt("• Adımlar telefonunun fiziksel hareket sensöründen gelir.\n• Mesafe = adım sayısı × adım uzunluğu.\n• Kalori ve aktif dakika tahminidir.\n• GPS ve internet gerekmez.\n• Bildirim ve ana ekran aynı adımları okur.\n• Telefon sensörü birkaç saniye gecikmeli adım gönderebilir.\n• Bildirimde takip açıkken sensör kullanılmaya devam edilir.\n• Pili korumak için sürekli GPS kullanılmaz.",13,false,muted),12);
        action(content,"HAREKET İZNİNİ KONTROL ET",()->beginTracking());
        action(content,"GEÇMİŞİ SIFIRLA",()->new AlertDialog.Builder(this).setTitle("Adım kayıtları silinsin mi?")
            .setMessage("Son 30 gün ve diğer tüm birikmiş adımlar bu cihazdan silinecek. Geri alınamaz.")
            .setNegativeButton("İptal",null).setPositiveButton("Sil",(dialog,which)->{StepStore.clearHistory(this);render();}).show());
        add(content,txt("Adım+ • v1.2 Test  |  Çevrimdışı  |  Hesap gerekmez",11,false,muted),18);
    }
    void setting(LinearLayout parent,String title,String v,String key,int min,int max){
        LinearLayout row=line();row.setPadding(dp(8),dp(14),dp(8),dp(14));
        TextView t=txt(title,14,true,fg);row.addView(t,new LinearLayout.LayoutParams(0,-2,1));
        row.addView(txt(v+"  ›",14,true,MINT));
        parent.addView(row);row.setOnClickListener(view->{
            EditText input=new EditText(this);input.setInputType(InputType.TYPE_CLASS_NUMBER);
            int old=StepStore.prefs(this).getInt(key,key.equals("goal")?8000:key.equals("height")?175:key.equals("weight")?75:StepStore.stride(this));
            input.setText(String.valueOf(old));input.setSelection(input.length());
            LinearLayout wrap=new LinearLayout(this);wrap.setPadding(dp(24),0,dp(24),0);wrap.addView(input,new LinearLayout.LayoutParams(-1,-2));
            new AlertDialog.Builder(this).setTitle(title).setMessage("Aralık: "+min+" - "+max)
                .setView(wrap).setNegativeButton("Vazgeç",null)
                .setPositiveButton("Kaydet",(d,w)->{
                    try {int num=Integer.parseInt(input.getText().toString().trim());
                        if(num<min||num>max){Toast.makeText(this,"Geçerli aralık: "+min+" - "+max,Toast.LENGTH_LONG).show();return;}
                        StepStore.prefs(this).edit().putInt(key,num).apply();render();
                    }catch(Exception ex){Toast.makeText(this,"Geçerli sayı gir",Toast.LENGTH_SHORT).show();}
                }).show();
        });
    }
    void action(LinearLayout parent,String name,Runnable fn){
        TextView t=txt(name,12,true,0xFF052027);t.setGravity(Gravity.CENTER);
        t.setPadding(dp(12),dp(15),dp(12),dp(15));t.setBackground(shape(MINT,15,0));
        add(parent,t,14);t.setOnClickListener(v->fn.run());
    }
    void summary(LinearLayout parent,String name,String value){
        LinearLayout r=line();r.setPadding(0,dp(14),0,0);
        r.addView(txt(name,13,false,muted),new LinearLayout.LayoutParams(0,-2,1));r.addView(txt(value,14,true,fg));parent.addView(r);
    }
    void share(){
        int n=StepStore.steps(this,0);
        String report="Bugün "+fmt(n)+" adım attım! 🚶\nMesafe: "+km(StepStore.distanceKm(this,n))+" km\nTahmini kalori: "+StepStore.calories(this,n)+" kcal\nAdım+ ile hareketlerimi takip ediyorum.";
        Intent i=new Intent(Intent.ACTION_SEND);i.setType("text/plain");i.putExtra(Intent.EXTRA_TEXT,report);startActivity(Intent.createChooser(i,"Günlük sonucu paylaş"));
    }
    void beginTracking(){
        SensorManager sm=(SensorManager)getSystemService(SENSOR_SERVICE);
        if(sm.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)==null&&sm.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)==null){
            new AlertDialog.Builder(this).setTitle("Adım sensörü bulunamadı").setMessage("Bu telefon fiziksel adım sayma sensörünü desteklemiyor. Yanlış sonuç göstermemek için sahte sayım yapmıyoruz.").setPositiveButton("Tamam",null).show();return;
        }
        if(Build.VERSION.SDK_INT>=29&&checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)!=PackageManager.PERMISSION_GRANTED){
            requestPermissions(new String[]{Manifest.permission.ACTIVITY_RECOGNITION},101);return;
        }
        try {startForegroundService(new Intent(this,StepService.class));}catch(Exception ex){Toast.makeText(this,"Takip başlatılamadı: "+ex.getMessage(),Toast.LENGTH_LONG).show();}
        if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},102);
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] grants){
        super.onRequestPermissionsResult(request,permissions,grants);
        if(request==101){if(grants.length>0&&grants[0]==PackageManager.PERMISSION_GRANTED)beginTracking();
            else Toast.makeText(this,"Adımların sayılması için hareket izni gerekli.",Toast.LENGTH_LONG).show();}
        render();
    }
    class Ring extends View{
        int n;final int g;Paint p=new Paint(3);ValueAnimator animator;
        Ring(int n,int g){super(MainActivity.this);this.n=n;this.g=g;}
        void setSteps(int count){
            if(count==n)return;
            if(animator!=null)animator.cancel();
            animator=ValueAnimator.ofInt(n,count);animator.setDuration(280);
            animator.addUpdateListener(a->{n=(Integer)a.getAnimatedValue();invalidate();});
            animator.start();
        }
        @Override protected void onDraw(Canvas c){
            super.onDraw(c);float cx=getWidth()/2f,cy=getHeight()/2f,r=dp(72);
            p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(dp(13));p.setStrokeCap(Paint.Cap.ROUND);
            p.setColor(border);c.drawArc(cx-r,cy-r,cx+r,cy+r,-90,360,false,p);
            p.setColor(MINT);c.drawArc(cx-r,cy-r,cx+r,cy+r,-90,360*Math.min(1f,n/(float)Math.max(1,g)),false,p);
            p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.CENTER);
            p.setColor(muted);p.setTextSize(dp(13));p.setTypeface(Typeface.DEFAULT_BOLD);c.drawText("BUGÜN",cx,cy-dp(26),p);
            p.setColor(fg);p.setTextSize(dp(39));c.drawText(fmt(n),cx,cy+dp(17),p);
            p.setColor(MINT);p.setTextSize(dp(13));c.drawText("ADIM",cx,cy+dp(42),p);
        }
    }
    class Bars extends View{
        int[] a;final int count;Paint p=new Paint(3);
        Bars(int[] a,int count){super(MainActivity.this);this.a=a;this.count=count;}
        void setValues(int[] values){a=values;invalidate();}
        @Override protected void onDraw(Canvas c){
            float W=getWidth(),H=getHeight(),top=dp(15),bottom=H-dp(27);
            int max=1;for(int x:a)max=Math.max(max,x);
            p.setColor(border);p.setStrokeWidth(dp(1));for(int i=0;i<3;i++){float y=top+(bottom-top)*i/2;c.drawLine(0,y,W,y,p);}
            float unit=W/count;
            for(int i=0;i<count;i++){
                float height=(bottom-top)*a[i]/max;float x=i*unit+unit*.17f;
                p.setColor(a[i]==max&&a[i]>0?MINT:BLUE);p.setStyle(Paint.Style.FILL);
                c.drawRoundRect(x,bottom-Math.max(dp(2),height),x+unit*.66f,bottom,dp(4),dp(4),p);
            }
            p.setTextSize(dp(10));p.setColor(muted);p.setTextAlign(Paint.Align.CENTER);
            if(count==24)for(int i=0;i<24;i+=6)c.drawText(String.format(TR,"%02d",i),unit*(i+.5f),H-dp(6),p);
            else {for(int i=0;i<7;i++){Calendar date=Calendar.getInstance();date.add(Calendar.DAY_OF_YEAR,i-6);
                String label=new SimpleDateFormat("EEE",TR).format(date.getTime());
                c.drawText(label,unit*(i+.5f),H-dp(5),p);}}
        }
    }
    class Trend extends View{
        final int[] a;Paint p=new Paint(3);Trend(int[] a){super(MainActivity.this);this.a=a;}
        @Override protected void onDraw(Canvas c){
            super.onDraw(c);float w=getWidth(),h=getHeight(),top=dp(14),bottom=h-dp(25);
            int max=1;for(int n:a)max=Math.max(max,n);
            p.setColor(border);p.setStrokeWidth(dp(1));for(int j=0;j<3;j++){float y=top+j*(bottom-top)/2;c.drawLine(0,y,w,y,p);}
            Path path=new Path();
            for(int i=0;i<a.length;i++){float x=i*w/(a.length-1f),y=bottom-(bottom-top)*a[i]/max;if(i==0)path.moveTo(x,y);else path.lineTo(x,y);}
            p.setColor(MINT);p.setStrokeWidth(dp(2.8f));p.setStrokeJoin(Paint.Join.ROUND);p.setStrokeCap(Paint.Cap.ROUND);p.setStyle(Paint.Style.STROKE);c.drawPath(path,p);p.setStyle(Paint.Style.FILL);
            p.setTextAlign(Paint.Align.LEFT);p.setTextSize(dp(10));p.setColor(muted);c.drawText("30 gün önce",0,h-dp(4),p);
            p.setTextAlign(Paint.Align.RIGHT);c.drawText("Bugün",w,h-dp(4),p);
        }
    }
}
