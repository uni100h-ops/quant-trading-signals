"""QTS customer walkthrough. Illustrations and historical figures, not live trades."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
import subprocess,json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'media'
W,H,FPS,DURATION=1280,720,6,96
BG='#09111d';PANEL='#142238';LINE='#304662';WHITE='#f2f6fb';MUTED='#b5c4da';CYAN='#69d8ff';GREEN='#65e0b3';AMBER='#ffce76'
def font(size,mono=False):
    return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/'+('DejaVuSansMono.ttf' if mono else 'DejaVuSans.ttf'),size)
F={n:font(n) for n in (14,16,18,20,22,26,30,34,42,48,68)}
M={n:font(n,True) for n in (16,18,20)}
SCENES=[
 (0,12,'01 / Your local trading agent','Download. Set up. Start.',[
  'Automated BTC/USDC trading on Hyperliquid.',
  'Market monitoring, account-based sizing and a dynamic stop.',
  'No TradingView subscription. No product website.',
  'You control your wallets and can stop the agent.']),
 (12,24,'02 / Installation','Three files to know',[
  '1. Extract the complete Windows installer ZIP.',
  '2. Open INSTALAR.bat and follow the setup steps.',
  '3. Open INICIAR.bat when setup is complete.',
  'Try PROBAR_PAPER.bat first: simulated trades, no payments.']),
 (24,39,'03 / Wallet setup','Two separate wallets. Clear permissions.',[
  'Trading: your Hyperliquid account + a separate API wallet key.',
  'Payments: USDC on an activated secondary Algorand wallet.',
  'Recurring network fees are sponsored. No USDC-to-ALGO swap.',
  'New wallets need provider activation before a USDC deposit.',
  'Never enter your main trading wallet seed.']),
 (39,51,'04 / Your keys','Saved in your computer\'s password manager',[
  'Windows Credential Manager / macOS Keychain / Secret Service',
  'Keys are not stored in config.txt or log.txt.',
  'They are reused on restart. Keep your computer secure.',
  'Review recipients and prices, then type ACCEPT.']),
 (51,69,'05 / Fees','Know exactly who receives each fee',[]),
 (69,81,'06 / Console and logs','Follow each action in English',[
  '2026-09-22 10:00:02 | NO OPEN POSITIONS | scanning BTC/USDC',
  '2026-09-22 10:05:04 | X402 PAYMENT CONFIRMED',
  '2026-09-22 10:05:05 | OPEN | BUY | initial stop confirmed',
  '2026-09-22 11:35:02 | STOP RAISED | indicators favorable',
  '2026-09-22 12:10:02 | CLOSE CONFIRMED | resuming scanning']),
 (81,96,'07 / Historical backtest','Selected strategy · 12-month simulation',[])
]
def base(section,sec):
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
    d.text((42,28),'QUANT TRADING SIGNALS',font=F[26],fill=WHITE)
    d.text((835,34),section,font=F[16],fill=CYAN)
    d.line((42,78,1238,78),fill=LINE,width=2)
    d.rectangle((42,645,1238,649),fill=PANEL);d.rectangle((42,645,42+1196*sec/DURATION,649),fill=CYAN)
    footer='ILLUSTRATED WALKTHROUGH / NO REAL ORDERS OR PAYMENTS'
    d.text((42,673),footer,font=F[14],fill=MUTED)
    return im,d

def put(d,xy,text,size=22,color=WHITE,mono=False):
    chosen=(M if mono else F)[size]
    box=d.textbbox(xy,text,font=chosen)
    if box[2]>W-40:raise ValueError('Text exceeds video width: '+text)
    d.text(xy,text,font=chosen,fill=color)

def frame(sec):
    start,end,section,title,items=next(s for s in SCENES if s[0]<=sec<s[1])
    im,d=base(section,sec);put(d,(44,113),title,34)
    if start==51:
        rows=[('QTS developer','0.01%','Each filled entry/addition'),
              ('Hyperliquid exchange','0.045% base taker','Each market fill; tier may vary'),
              ('QTS x402 service','0.01 USDC','Activation / risk check / exit receipt'),
              ('Algorand network','No extra customer charge','Covered by the network sponsor'),
              ('Funding','Variable','Paid or received while open')]
        d.rounded_rectangle((42,202,1238,570),radius=16,fill=PANEL)
        for i,(name,price,detail) in enumerate(rows):
            y=226+i*63
            put(d,(64,y),name,20);put(d,(341,y),price,20,GREEN);put(d,(698,y),detail,18,MUTED)
        put(d,(52,592),'Builder Fee = the QTS developer commission, separate from the exchange fee.',20,AMBER)
    elif start==81:
        put(d,(52,192),'+238.57%',68,GREEN)
        put(d,(580,206),'Maximum drawdown: 35.04%',30,AMBER)
        put(d,(580,256),'10,000 → 33,857.42 USDC',26)
        put(d,(55,326),'BTCUSDC / 5 minutes / 21 Sep 2025 – 21 Sep 2026',26)
        put(d,(55,374),'65.12% winning positions · Profit factor 1.742',22)
        d.rounded_rectangle((42,426,1238,614),radius=16,fill=PANEL)
        for i,line in enumerate([
          'Historical Binance-data simulation with modeled costs.',
          'EXCLUDES new developer/x402 fees and live service checks.',
          'Not a new fee-adjusted Hyperliquid backtest. Future returns are not guaranteed.',
          'Preview release: provider activation and funded tests are still required.']):
            put(d,(62,444+i*40),line,20,AMBER if i==1 else MUTED)
    else:
        d.rounded_rectangle((42,208,1238,603),radius=16,fill=PANEL)
        if start==69:put(d,(64,226),'Illustrated events — not a recording of live trades',18,AMBER)
        for i,line in enumerate(items):
            y=266+i*(58 if start==69 else 60)
            put(d,(64,y),line,18 if start==69 else 22,WHITE,mono=start==69)
    return im

def main():
    OUT.mkdir(exist_ok=True);path=OUT/'QUANT_TRADING_SIGNALS_Demo.mp4'
    command=['ffmpeg','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-',
             '-an','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-movflags','+faststart',str(path)]
    with (OUT/'video_render.log').open('w') as log:
        p=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=log)
        for i in range(DURATION*FPS):p.stdin.write(frame(i/FPS).tobytes())
        p.stdin.close()
        if p.wait():raise RuntimeError('Video rendering failed')
    frame(87).save(OUT/'poster.jpg',quality=92)
    texts=[
      'QTS is a local BTC/USDC trading agent. It monitors markets and manages a dynamic stop. No TradingView subscription is required.',
      'Extract the installer ZIP. Run INSTALAR.bat for guided setup, then INICIAR.bat to start. PROBAR_PAPER.bat runs without keys or payments.',
      'Use separate trading and payment wallets. Recurring Algorand fees are sponsored. New wallets require provider activation before depositing USDC.',
      'Keys are saved in your operating system password manager, not in configuration or logs. Review the recipients and fees before typing ACCEPT.',
      'The developer fee is 0.01 percent per filled entry or addition. Hyperliquid has a separate base taker fee of 0.045 percent. QTS service calls cost 0.01 USDC. Sponsored network fees add no customer charge. Funding varies.',
      'English timestamped logs show scans, entries, stop adjustments, payments and exits. These examples are illustrated, not live trades.',
      'The earlier annual simulation returned 238.57 percent with 35.04 percent maximum drawdown. It excludes the new developer and x402 fees and live audit filters. This is not a new net-return backtest or a promise of future gains.'
    ]
    ts=lambda n:f'00:{n//60:02}:{n%60:02}.000'
    (OUT/'QUANT_TRADING_SIGNALS_Demo.vtt').write_text('WEBVTT\n\n'+'\n\n'.join(f'{ts(s[0])} --> {ts(s[1])}\n{t}' for s,t in zip(SCENES,texts))+'\n')
    (OUT/'video_metadata.json').write_text(json.dumps({'project':'QUANT TRADING SIGNALS','seconds':DURATION,'fps':FPS,'language':'English','audio':False,'type':'illustrated customer walkthrough','real_trades':False,'real_payments':False,'historical_reference_return_pct':238.5741814079771,'new_payment_costs_included_in_reference':False,'mainnet_verified':False},indent=2)+'\n')
    print(json.dumps({'video':str(path),'bytes':path.stat().st_size,'seconds':DURATION}))
if __name__=='__main__':main()
