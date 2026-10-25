package com.fatih.adimsayar;

import android.Manifest;
import android.app.*;
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
    private static final int MINT=0x58F5DB;
    private static final int BLUE=0x60B8FF;
    private boolean dark=true;
    private int bg,card,fg,muted,border,smallCard;
    private int page=0;
    private LinearLayout root,content,nav;
    private ScrollView scroll;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private int displayed=-1;
    private final Runnable tick=new Runnable(){public void run(){
        if(page==0 && StepStore.steps(MainActivity.this,0)!=displayed)render();
        handler.postDelayed(this,5000);
    }};
    int dp(float f){return (int)(getResources().getDisplayMetrics().density*f+0.5f);}
    private final Locale TR=new Locale("tr","TR");
    @Override public void onCreate(Bundle b){super.onCreate(b);dark=StepStore.prefs(this).getBoolean("dark",true);render();beginTracking();}
    @Override protected void onResume(){super.onResume();handler.removeCallbacks(tick);handler.postDelayed(tick,5000);}
    @Override protected void onPause(){handler.removeCallbacks(tick);super.onPause();}
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
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(bg);
        setContentView(root);
        LinearLayout head=line();head.setPadding(dp(22),dp(18),dp(22),dp(12));
        TextView logo=txt("ADIM+",26,true,MINT);
        head.addView(logo,new LinearLayout.LayoutParams(0,-2,1));
        TextView ver=txt("●  TEST 1.0",11,true,muted);head.addView(ver);
        root.addView(head);
        scroll=new ScrollView(this);scroll.setFillViewport(false);scroll.setVerticalScrollBarEnabled(false);
        content=new LinearLayout(this);content.setPadding(dp(18),dp(6),dp(18),dp(26));content.setOrientation(1);scroll.addView(content);
        root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        if(page==0)today();else if(page==1)analysis();else if(page==2)history();else settings();
        nav=line();nav.setPadding(dp(8),dp(10),dp(8),dp(12));nav.setBackground(shape(card,18,0));
        String[] icons={"◉","▥","▤","⚙"};String[] titles={"Bugün","Analiz","Geçmiş","Ayarlar"};
        for(int i=0;i<4;i++){
            final int tab=i;LinearLayout cell=new LinearLayout(this);cell.setOrientation(1);cell.setGravity(Gravity.CENTER);
            cell.setPadding(dp(2),dp(7),dp(2),dp(5));cell.setBackground(shape(i==page?(dark?0xFF234D52:0xFFDBF7F1):card,16,0));
            TextView icon=txt(icons[i],21,true,i==page?MINT:muted);icon.setGravity(Gravity.CENTER);cell.addView(icon);
            TextView title=txt(titles[i],11,i==page,i==page?fg:muted);title.setGravity(Gravity.CENTER);cell.addView(title);
            nav.addView(cell,new LinearLayout.LayoutParams(0,dp(60),1));cell.setOnClickListener(v->{page=tab;render();});
        }
        root.addView(nav);
    }
    private void headline(String title,String subtitle){add(content,txt(title,24,true,fg),8);add(content,txt(subtitle,13,false,muted),5);}
    private void today(){
        String date=new SimpleDateFormat("EEEE, d MMMM yyyy",TR).format(new Date());
        headline("Bugünkü hareketin",date);
        int steps=StepStore.steps(this,0),goal=StepStore.goal(this);
        LinearLayout hero=box(18);add(content,hero,20);
        Ring ring=new Ring(steps,goal);hero.addView(ring,new LinearLayout.LayoutParams(-1,dp(236)));
        TextView msg=txt(steps>=goal?"HEDEFİ TAMAMLADIN ✦":"HEDEFE "+fmt(Math.max(0,goal-steps))+" ADIM KALDI",13,true,steps>=goal?MINT:muted);
        msg.setGravity(Gravity.CENTER);hero.addView(msg);
        TextView percent=txt("Günlük hedef: "+fmt(goal)+" adım  •  %"+(steps*100/Math.max(1,goal)),12,false,muted);
        percent.setGravity(Gravity.CENTER);add(hero,percent,9);
        LinearLayout status=line();status.setPadding(dp(14),dp(12),dp(14),dp(12));status.setBackground(shape(smallCard,12,0));add(content,status,14);
        SensorManager sm=(SensorManager)getSystemService(SENSOR_SERVICE);
        boolean has=sm.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)!=null||sm.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)!=null;
        boolean perm=Build.VERSION.SDK_INT<29||checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)==PackageManager.PERMISSION_GRANTED;
        TextView info=txt(!has?"Bu telefonda adım sensörü bulunamadı":!perm?"Hareket iznine dokunarak başlat":"●  Sensör ile gerçek adım takibi etkin",12,true,has&&perm?MINT:0xFFFFB86B);
        status.addView(info,new LinearLayout.LayoutParams(0,-2,1));status.setOnClickListener(v->beginTracking());
        LinearLayout row=line();add(content,row,15);
        stat(row,"MESAFE",km(StepStore.distanceKm(this,steps))+" km","↗",BLUE);
        stat(row,"KALORİ",fmt(StepStore.calories(this,steps))+" kcal","♨",MINT);
        row=line();add(content,row,11);
        stat(row,"AKTİF SÜRE*","~"+fmt(StepStore.activeMinutes(steps))+" dk","◷",MINT);
        stat(row,"TOPLAM ADIM",fmt((int)Math.min(Integer.MAX_VALUE,StepStore.prefs(this).getLong("lifetime",0))) ,"⌁",BLUE);
        LinearLayout chart=box(15);add(content,chart,16);label(chart,"Saatlik hareket","Bugün saatlere göre adım dağılımı");
        chart.addView(new Bars(hourData(),24),new LinearLayout.LayoutParams(-1,dp(154)));
        LinearLayout hint=box(15);add(content,hint,13);
        hint.addView(txt("✦   Her adım bir ilerleme",15,true,fg));
        add(hint,txt("Günlük veriler telefonunda saklanır. Mesafe, yakılan kalori ve aktif süre yaklaşık değerlerdir. Sayım uygulamayı ilk açtığından itibaren başlar.",12,false,muted),8);
        action(content,"BUGÜNKÜ SONUCU PAYLAŞ",()->share());
    }
    private void stat(LinearLayout row,String label,String value,String sym,int accent){
        LinearLayout card1=box(15);
        LinearLayout top=line();top.addView(txt(label,11,true,muted),new LinearLayout.LayoutParams(0,-2,1));
        top.addView(txt(sym,22,true,accent));card1.addView(top);
        add(card1,txt(value,22,true,fg),15);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(0,dp(112),1);
        params.setMargins(dp(4),0,dp(4),0);row.addView(card1,params);
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
        LinearLayout m=box(16);add(content,m,13);
        label(m,"30 günlük trend",fmt(month)+" adım toplam • "+fmt(month/30)+" adım/gün");
        int[] thirty=new int[30];for(int i=0;i<30;i++)thirty[i]=StepStore.steps(this,i-29);
        m.addView(new Trend(thirty),new LinearLayout.LayoutParams(-1,dp(160)));
        LinearLayout p=box(17);add(content,p,14);label(p,"Kişisel istatistikler","Kendi kayıtlarına göre");
        summary(p,"En yüksek gün",fmt(StepStore.record(this))+" adım");
        summary(p,"Hedef serisi",StepStore.streak(this)+" gün");
        summary(p,"Son 30 günde hedef",StepStore.goalDays(this)+" / 30 gün");
        summary(p,"30 günlük mesafe",km(StepStore.distanceKm(this,month))+" km");
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
            add(labels,txt(km(StepStore.distanceKm(this,s))+" km  •  "+StepStore.calories(this,s)+" kcal",11,false,muted),4);
            r.addView(labels,new LinearLayout.LayoutParams(0,-2,1));
            r.addView(txt(fmt(s)+" "+(s>=g?"✓":""),15,true,s>=g?MINT:fg));
            ProgressBar pb=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
            pb.setMax(g);pb.setProgress(Math.min(g,s));pb.setProgressTintList(android.content.res.ColorStateList.valueOf(MINT));
            pb.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(border));
            add(box,pb,9);
        }
        add(content,txt("Geçmiş sadece uygulamanın sensörle topladığı adımları içerir; başka uygulamaların kayıtları otomatik aktarılmaz.",12,false,muted),15);
    }
    void settings(){
        headline("Kişisel ayarlar","Hedeflerini ve ölçümlerini düzenle");
        LinearLayout b=box(10);add(content,b,20);
        setting(b,"Günlük adım hedefi",fmt(StepStore.goal(this))+" adım","goal",2000,50000);
        setting(b,"Boy",StepStore.height(this)+" cm","height",100,230);
        setting(b,"Kilo",StepStore.weight(this)+" kg","weight",30,250);
        setting(b,"Adım uzunluğu",StepStore.stride(this)+" cm","stride",30,150);
        LinearLayout theme=box(15);add(content,theme,15);
        LinearLayout t=line();t.addView(txt("Koyu tema",16,true,fg),new LinearLayout.LayoutParams(0,-2,1));
        Switch sw=new Switch(this);sw.setChecked(dark);t.addView(sw);theme.addView(t);
        add(theme,txt("Açık / koyu görünüm arasında geçiş yap",12,false,muted),7);
        sw.setOnCheckedChangeListener((button,isChecked)->{StepStore.prefs(this).edit().putBoolean("dark",isChecked).apply();dark=isChecked;render();});
        LinearLayout tips=box(16);add(content,tips,14);
        label(tips,"Nasıl ölçülüyor?","Verilerini doğru yorumla");
        add(tips,txt("• Adımlar telefonunun fiziksel hareket sensöründen gelir.\n• Mesafe = adım sayısı × adım uzunluğu.\n• Kalori ve aktif dakika tahminidir.\n• GPS ve internet gerekmez.\n• Bildirimde takip açıkken sensör kullanılmaya devam edilir.\n• Pili korumak için sürekli GPS kullanılmaz.",13,false,muted),12);
        action(content,"HAREKET İZNİNİ KONTROL ET",()->beginTracking());
        action(content,"GEÇMİŞİ SIFIRLA",()->new AlertDialog.Builder(this).setTitle("Adım kayıtları silinsin mi?")
            .setMessage("Son 30 gün ve diğer tüm birikmiş adımlar bu cihazdan silinecek. Geri alınamaz.")
            .setNegativeButton("İptal",null).setPositiveButton("Sil",(dialog,which)->{StepStore.clearHistory(this);render();}).show());
        add(content,txt("Adım+ • v1.0 Test  |  Çevrimdışı  |  Hesap gerekmez",11,false,muted),18);
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
        final int n,g;Paint p=new Paint(3);
        Ring(int n,int g){super(MainActivity.this);this.n=n;this.g=g;}
        @Override protected void onDraw(Canvas c){
            super.onDraw(c);float cx=getWidth()/2f,cy=getHeight()/2f,r=dp(85);
            p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(dp(17));p.setStrokeCap(Paint.Cap.ROUND);
            p.setColor(border);c.drawArc(cx-r,cy-r,cx+r,cy+r,-90,360,false,p);
            p.setColor(MINT);c.drawArc(cx-r,cy-r,cx+r,cy+r,-90,360*Math.min(1f,n/(float)Math.max(1,g)),false,p);
            p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.CENTER);
            p.setColor(muted);p.setTextSize(dp(13));p.setTypeface(Typeface.DEFAULT_BOLD);c.drawText("BUGÜN",cx,cy-dp(26),p);
            p.setColor(fg);p.setTextSize(dp(41));c.drawText(fmt(n),cx,cy+dp(17),p);
            p.setColor(MINT);p.setTextSize(dp(13));c.drawText("ADIM",cx,cy+dp(42),p);
        }
    }
    class Bars extends View{
        final int[] a;final int count;Paint p=new Paint(3);
        Bars(int[] a,int count){super(MainActivity.this);this.a=a;this.count=count;}
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
