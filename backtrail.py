import tkinter as tk, json, os, sys, time, subprocess, threading, re, webbrowser
import concurrent.futures as cf
from tkinter import ttk,filedialog,messagebox,scrolledtext
from pathlib import Path
from datetime import datetime
from backup_engine import Backup, TOOL, TAGLINE, run, nworkers, CANCEL, cli_note, identity, list_backups, source_folders, compare, prune, load_manifest, VERSION
from backup_engine import signing_available, signer_id, load_or_create_key, key_fingerprint, _raw_pub, keys_dir, verify_archive, SIG_MARKS
from backup_engine import restore, restore_plan, restore_to_source, source_of, location_for, list_restores, RESTORE_RE, safe_name

BASE=Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parent
CF=BASE/"config.json"; STATE=BASE/"state.json"
def jload(p,d):
 try:return json.loads(p.read_text(encoding="utf-8"))
 except:return d
cfg=jload(CF,{"backup_root":str(BASE/"backups"),"archive_format":"zip","rar_path":r"C:\Program Files\WinRAR\Rar.exe","locations":[]})
state=jload(STATE,{})
def save(): CF.write_text(json.dumps(cfg,indent=2),encoding="utf-8")
def savestate(): STATE.write_text(json.dumps(state,indent=2),encoding="utf-8")

SCHED_TASK="Backtrail Daily Backup";LOGF=BASE/"backtrail_scheduled.log"
NOWINF=getattr(subprocess,"CREATE_NO_WINDOW",0)
def headless():
 # invoked by the scheduled task as: Backtrail.exe --run   (no GUI, logs to backtrail_scheduled.log)
 try:
  if LOGF.exists() and LOGF.stat().st_size>1_000_000:
   LOGF.write_text("\n".join(LOGF.read_text(encoding="utf-8",errors="replace").splitlines()[-2000:])+"\n",encoding="utf-8")
 except OSError:pass
 lw=threading.Lock()
 def clog(m,src=None):
  line=f"{datetime.now():%Y-%m-%d %H:%M:%S}  "+(f"[{src}] " if src else "")+m
  with lw:
   try:
    with LOGF.open("a",encoding="utf-8") as fh:fh.write(line+"\n")
   except OSError:pass
   try:print(line)
   except Exception:pass
 locs=cfg.get("locations") or []
 note=cli_note(sys.argv)   # Backtrail.exe --run --note "why": stamped into the manifest and the changelog
 if not locs:clog("Scheduled run: no backup sources configured.");return 1
 try:P=max(1,min(int(cfg.get("parallel_backups",3) or 1),len(locs)))
 except Exception:P=1
 rc=dict(cfg)
 if not cfg.get("workers"):rc["workers"]=max(4,nworkers(cfg)//P)
 clog(f"Scheduled run starting: {len(locs)} source(s), up to {P} at a time.")
 fails=[];lk={};kl=threading.Lock()
 def keylock(k):
  with kl:return lk.setdefault(k,threading.Lock())
 def one(l):
  key=l.get("backup_name") or l.get("name")
  try:
   with keylock(key):
    cp=run(l,rc,lambda m,k=key:clog(m,k),note=note,mode="scheduled")
    state[key]={"time":datetime.now().isoformat(timespec="seconds"),"changelog":str(cp),"user":identity()[0],"mode":"scheduled","note":note}
  except Exception as e:clog("ERROR: "+str(e),key);fails.append(key)
 with cf.ThreadPoolExecutor(max_workers=P) as ex:
  for _ in ex.map(one,locs):pass
 try:savestate()
 except Exception:pass
 clog(f"Scheduled run finished: {len(locs)-len(fails)} ok, {len(fails)} failed.")
 return 1 if fails else 0
if any(a.lower() in ("--run","/run") for a in sys.argv[1:]):sys.exit(headless())
VLOGF=BASE/"backtrail_verify.log"
def verify_cli():
 # Backtrail.exe --verify [folder]: every backup under the backup root (or the folder given) is checked --
 # signature against the manifest, then the archive re-hashed. Exit 0 = all good, 1 = a problem.
 # The exe has no console, so the report also goes to backtrail_verify.log next to it.
 args=[a for a in sys.argv[1:] if not a.startswith(("-","/"))]
 root=Path(args[0]) if args else Path(cfg["backup_root"])
 lines=[f"{TOOL} v{VERSION} signature check  {datetime.now():%Y-%m-%d %H:%M:%S}  by {signer_id()}",f"Root: {root}",""]
 folders=source_folders(root) or ([root] if list_backups(root) else [])
 bad=n=0
 for d in folders:
  for b in list_backups(d):
   r=verify_archive(b);n+=1
   if r["status"] in ("invalid","untrusted"):bad+=1
   lines.append(f"{SIG_MARKS.get(r['status'],r['status']):16} {d.name} | {b.stamp} | {r['detail']}")
 lines.append("");lines.append(f"{n} backup(s) checked, {bad} problem(s).")
 try:VLOGF.write_text("\n".join(lines)+"\n",encoding="utf-8")
 except OSError:pass
 for l in lines:
  try:print(l)
  except Exception:pass
 return 1 if bad else 0
if any(a.lower() in ("--verify","/verify") for a in sys.argv[1:]):sys.exit(verify_cli())
def log(s,src=None):
 tag="warn" if s.startswith("WARNING") else "err" if s.startswith("ERROR") else "ok" if s.startswith("SUCCESS") else "info"
 def _():
  console.insert("end",f"{datetime.now():%H:%M:%S}  ",("ts",))
  if src:console.insert("end",f"[{src}] ",("src",))
  console.insert("end",f"{s}\n",(tag,));console.see("end")
 root.after(0,_)
def backup_root(): return Path(cfg["backup_root"])
def changelogs():
 b=backup_root()
 return sorted(b.rglob("*_CHANGELOG.txt"),key=lambda p:p.stat().st_mtime,reverse=True) if b.exists() else []
def manifests():
 b=backup_root()
 return list(b.rglob("*_MANIFEST.json")) if b.exists() else []
def compares():
 # the changelogs written by "Compare Backups" (never counted as backups)
 b=backup_root()
 return sorted(b.rglob("*_COMPARE.txt"),key=lambda p:p.stat().st_mtime,reverse=True) if b.exists() else []
def restores():
 # the reports written by Restore
 b=backup_root()
 return sorted(b.rglob("*_RESTORE.txt"),key=lambda p:p.stat().st_mtime,reverse=True) if b.exists() else []

def parse_summary(p):
 t=p.read_text(encoding="utf-8",errors="replace")
 def n(k):
  m=re.search(rf"^{k}:\s*(\d+)",t,re.M);return int(m.group(1)) if m else 0
 return {"added":n("Added"),"modified":n("Modified"),"deleted":n("Deleted"),"text":t}

def path_online(p,timeout=2.5):
 # bounded existence check: an unreachable network path can block for a long
 # network timeout, so run it in a throwaway thread and give up after `timeout`.
 r={}
 def chk():
  try:r["v"]=os.path.exists(p)
  except OSError:r["v"]=False
 t=threading.Thread(target=chk,daemon=True);t.start();t.join(timeout)
 return r.get("v",False)

def refresh_sources():
 for x in tree.get_children():tree.delete(x)
 for i,l in enumerate(cfg["locations"]):
  key=l.get("backup_name") or l.get("name","")
  st=state.get(key,{})
  tree.insert("","end",iid=str(i),tags=("odd" if i%2 else "even",),values=(l.get("name",""),key,l["path"],
   "● …",st.get("time","Never").replace("T"," ")+(f"  ·  {st['user']}" if st.get("user") else "")))
 dest.set(cfg.get("backup_root",""));fmt.set(cfg.get("archive_format","zip").upper())
 keep_n.set(str(int(cfg.get("keep_last",0) or 0)));keep_d.set(str(int(cfg.get("keep_days",0) or 0)))
 try:comp.set(CLABEL.get(int(cfg.get("compress_level",6)),"Normal"))
 except Exception:comp.set("Normal")
 try:par.set(str(max(1,int(cfg.get("parallel_backups",3)))))
 except Exception:par.set("3")
 cards["sources"].config(text=str(len(cfg["locations"])))
 threading.Thread(target=_status_worker,daemon=True).start()

def _status_worker():
 # all potentially-slow filesystem access happens here, off the UI thread
 online=[path_online(l["path"]) for l in cfg["locations"]]
 total=latest=files=0
 try:
  logs=changelogs();total=len(logs)
  if logs:
   q=parse_summary(logs[0]);latest=q["added"]+q["modified"]+q["deleted"]
  ms=manifests()
  if ms:
   try:files=sum(len(load_manifest(p)[1]) for p in ms[-min(10,len(ms)):])
   except:pass
 except Exception:pass
 root.after(0,lambda:_apply_status(online,total,latest,files))

def _apply_status(online,total,latest,files):
 for i,ok in enumerate(online):
  if not tree.exists(str(i)):continue
  vals=list(tree.item(str(i),"values"));vals[3]="● Ready" if ok else "● Offline"
  tree.item(str(i),tags=("offline" if not ok else ("odd" if i%2 else "even"),),values=vals)
 n=len(cfg["locations"])
 cards["sources"].config(text=str(n))
 cards["ready"].config(text=f"{sum(online)}/{n}")
 cards["backups"].config(text=str(total))
 cards["changes"].config(text=str(latest))
 cards["files"].config(text=f"{files:,}")

def selected():
 s=tree.selection();return int(s[0]) if s else None
def edit(idx=None):
 l=cfg["locations"][idx].copy() if idx is not None else {"name":"","backup_name":"","path":""}
 w=tk.Toplevel(root);w.title("Backup Source");w.geometry("720x230");w.grab_set();w.configure(bg=BG)
 vs={k:tk.StringVar(value=l.get(k,"")) for k in ("name","backup_name","path")}
 for r,(k,t) in enumerate((("name","Display name"),("backup_name","Backup name"),("path","Folder to back up"))):
  ttk.Label(w,text=t).grid(row=r,column=0,padx=12,pady=10,sticky="w")
  ttk.Entry(w,textvariable=vs[k],width=70).grid(row=r,column=1,padx=5,sticky="ew")
 ttk.Button(w,text="Browse",command=lambda:vs["path"].set(filedialog.askdirectory() or vs["path"].get())).grid(row=2,column=2)
 def ok():
  d={k:v.get().strip() for k,v in vs.items()}
  if not d["path"]:return messagebox.showerror("Missing path","Select a folder.",parent=w)
  d["name"]=d["name"] or "Source";d["backup_name"]=d["backup_name"] or d["name"]
  if idx is None:cfg["locations"].append(d)
  else:cfg["locations"][idx]=d
  save();refresh_sources();w.destroy()
 ttk.Button(w,text="Save",command=ok).grid(row=4,column=1,pady=16,sticky="e");w.columnconfigure(1,weight=1)
def remove():
 i=selected()
 if i is not None and messagebox.askyesno("Remove","Remove selected source?"):cfg["locations"].pop(i);save();refresh_sources()
def destination():
 p=filedialog.askdirectory()
 if p:cfg["backup_root"]=p;save();refresh_sources()
def _task_target():
 if getattr(sys,"frozen",False):return f'"{sys.executable}" --run'
 pyw=Path(sys.executable).with_name("pythonw.exe")
 return f'"{pyw if pyw.exists() else sys.executable}" "{Path(__file__).resolve()}" --run'
def _sch(args):
 return subprocess.run(["schtasks"]+args,capture_output=True,text=True,creationflags=NOWINF)
def sched_exists():
 try:return _sch(["/Query","/TN",SCHED_TASK]).returncode==0
 except Exception:return False
def sched_set(hhmm):
 r=_sch(["/Create","/TN",SCHED_TASK,"/TR",_task_target(),"/SC","DAILY","/ST",hhmm,"/F"])
 if r.returncode!=0:raise RuntimeError((r.stderr or r.stdout).strip() or "schtasks failed")
def sched_clear():
 r=_sch(["/Delete","/TN",SCHED_TASK,"/F"])
 if r.returncode!=0:raise RuntimeError((r.stderr or r.stdout).strip() or "schtasks failed")
def schedule():
 w=tk.Toplevel(root);w.title("Backtrail - Daily Schedule");w.geometry("620x270");w.grab_set();w.configure(bg=BG)
 ttk.Label(w,text="Run all backups automatically every day",font=("Segoe UI",12,"bold")).pack(anchor="w",padx=18,pady=(18,2))
 ttk.Label(w,text="Creates a Windows Scheduled Task. It runs while you are logged on to this PC,\nwith no window; results are written to backtrail_scheduled.log.").pack(anchor="w",padx=18)
 st=tk.StringVar();ttk.Label(w,textvariable=st,font=("Segoe UI",10,"bold"),foreground=ACCENT).pack(anchor="w",padx=18,pady=(12,0))
 row=ttk.Frame(w);row.pack(anchor="w",padx=18,pady=12)
 cur=str(cfg.get("schedule_time","20:00"));hh0,_,mm0=cur.partition(":")
 hh=tk.StringVar(value=hh0.zfill(2) if hh0.isdigit() else "20");mm=tk.StringVar(value=mm0.zfill(2) if mm0.isdigit() else "00")
 ttk.Label(row,text="Every day at").pack(side="left")
 ttk.Combobox(row,textvariable=hh,values=[f"{i:02d}" for i in range(24)],state="readonly",width=4).pack(side="left",padx=(8,2))
 ttk.Label(row,text=":").pack(side="left")
 ttk.Combobox(row,textvariable=mm,values=["00","15","30","45"],state="readonly",width=4).pack(side="left",padx=2)
 def refresh():st.set(f"Currently scheduled daily at {cfg.get('schedule_time','?')}" if sched_exists() else "Not scheduled")
 def enable():
  t=f"{hh.get()}:{mm.get()}"
  try:
   sched_set(t);cfg["schedule_time"]=t;save();refresh();log(f"Daily backup scheduled at {t}.")
  except Exception as e:messagebox.showerror("Schedule failed",str(e),parent=w)
 def disable():
  try:
   sched_clear();refresh();log("Daily backup schedule removed.")
  except Exception as e:messagebox.showerror("Could not remove schedule",str(e),parent=w)
 b=ttk.Frame(w);b.pack(fill="x",padx=18,pady=(6,0))
 ttk.Button(b,text="Enable / Update",style="Accent.TButton",command=enable).pack(side="left")
 ttk.Button(b,text="Remove schedule",command=disable).pack(side="left",padx=8)
 ttk.Button(b,text="Close",command=w.destroy).pack(side="right")
 refresh()
CLEVEL={"Fast":1,"Normal":6,"Maximum":9};CLABEL={v:k for k,v in CLEVEL.items()}
def options():
 cfg["archive_format"]=fmt.get().lower();cfg["compress_level"]=CLEVEL.get(comp.get(),6)
 try:cfg["parallel_backups"]=max(1,int(par.get()))
 except Exception:cfg["parallel_backups"]=3
 for key,var in (("keep_last",keep_n),("keep_days",keep_d)):
  try:cfg[key]=max(0,int(var.get() or 0))
  except Exception:cfg[key]=0
  var.set(str(cfg[key]))
 ret=", ".join(x for x in (f"keep last {cfg['keep_last']}" if cfg["keep_last"] else "",f"keep {cfg['keep_days']} days" if cfg["keep_days"] else "") if x) or "keep everything"
 save();log(f"Settings saved (compression: {comp.get()}, parallel backups: {cfg['parallel_backups']}, retention: {ret}).")
def prune_now():
 kl=int(cfg.get("keep_last",0) or 0);kd=int(cfg.get("keep_days",0) or 0)
 if kl<=0 and kd<=0:return messagebox.showinfo("Backtrail","Set a retention first (keep last N backups and/or N days), then Save.")
 folders=source_folders(backup_root())
 if not folders:return messagebox.showinfo("Backtrail","No backups found under the destination.")
 what=", ".join(x for x in (f"the newest {kl}" if kl else "",f"the last {kd} days" if kd else "") if x)
 if not messagebox.askyesno("Prune old backups?",f"Remove every backup (archive, manifest and changelog) of the {len(folders)} source folder(s) except {what}?\n\nThe newest complete backup of each source is always kept."):return
 def work():
  n=0
  for d in folders:n+=len(prune(d,kl,kd,lambda m,k=d.name:log(m,k)))
  log(f"SUCCESS: retention applied, {n} old backup(s) removed." if n else "Retention applied: nothing to remove.")
  root.after(0,refresh_sources)
 threading.Thread(target=work,daemon=True).start()
_busy=[False];_quit=[False];_slock=threading.Lock();_klocks={};_kl=threading.Lock()
def _keylock(k):
 # sources that share a backup name write to the same folder: serialize those,
 # everything else runs concurrently.
 with _kl:return _klocks.setdefault(k,threading.Lock())
def _setbusy(b):
 for w in (btn_all,btn_sel):w.config(state="disabled" if b else "normal")
def _mark_done(i):
 if tree.exists(str(i)):
  v=list(tree.item(str(i),"values"));v[4]=datetime.now().strftime("%Y-%m-%d %H:%M:%S")+f"  ·  {identity()[0]}";tree.item(str(i),values=v)
def execute(one=False):
 if _busy[0]:return messagebox.showinfo("Backtrail","A backup run is already in progress.")
 ids=[selected()] if one and selected() is not None else list(range(len(cfg["locations"])))
 if not ids:return messagebox.showinfo("Backtrail","No backup source selected/configured.")
 jobs=[(i,dict(cfg["locations"][i])) for i in ids]
 try:P=max(1,min(int(cfg.get("parallel_backups",3) or 1),len(jobs)))
 except Exception:P=1
 rc=dict(cfg)
 if not cfg.get("workers"):rc["workers"]=max(4,nworkers(cfg)//P)   # keep total hashing threads roughly constant
 CANCEL.clear();_busy[0]=True;_setbusy(True)
 note=note_var.get().strip()   # why this backup is taken: goes into the manifest and the changelog with your name
 def one_src(job):
  i,l=job;key=l.get("backup_name") or l.get("name")
  try:
   with _keylock(key):
    cp=run(l,rc,lambda m,k=key:log(m,k),note=note,mode="manual")
    with _slock:
     state[key]={"time":datetime.now().isoformat(timespec="seconds"),"changelog":str(cp),"user":identity()[0],"mode":"manual","note":note};savestate()
    root.after(0,lambda i=i:_mark_done(i))
  except Exception as e:log(("WARNING: " if CANCEL.is_set() else "ERROR: ")+str(e),key)
 def work():
  try:
   if len(jobs)>1:log(f"Running {len(jobs)} backups, up to {P} at a time...")
   with cf.ThreadPoolExecutor(max_workers=P) as ex:
    for _ in ex.map(one_src,jobs):pass
   if len(jobs)>1:log("All backups finished.")
  finally:
   _busy[0]=False
   if not _quit[0]:root.after(0,lambda:(_setbusy(False),refresh_sources()))
 threading.Thread(target=work,daemon=True).start()
def on_close():
 if not _busy[0]:return root.destroy()
 if not messagebox.askyesno("Backup in progress","A backup is still running.\n\nQuit now? The running backup will be stopped and its incomplete archive removed."):return
 _quit[0]=True;CANCEL.set();_setbusy(True);log("WARNING: Stopping running backups...")
 deadline=time.time()+20
 def wait():
  if _busy[0] and time.time()<deadline:root.after(150,wait)
  else:root.destroy()
 wait()

def textpane(parent):
 # a read-only-looking monospace text with vertical AND horizontal scrollbars (hash lines are long)
 f=ttk.Frame(parent);f.rowconfigure(0,weight=1);f.columnconfigure(0,weight=1)
 tx=tk.Text(f,font=("Consolas",10),wrap="none",bg=CARD,fg=TEXT,relief="flat",highlightthickness=1,highlightbackground=BORDER,padx=6,pady=4)
 vs=ttk.Scrollbar(f,orient="vertical",command=tx.yview);hs=ttk.Scrollbar(f,orient="horizontal",command=tx.xview)
 tx.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
 tx.grid(row=0,column=0,sticky="nsew");vs.grid(row=0,column=1,sticky="ns");hs.grid(row=1,column=0,sticky="ew")
 return f,tx
def _report_rows(d):
 # the COMPARE and RESTORE reports of a source folder as Version History rows: (sort, kind, when, note, path)
 rows=[]
 for p in d.glob("*_COMPARE.txt"):
  stamps=re.findall(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}",p.name)
  rows.append((stamps[-1] if stamps else "","COMPARE",stamps[-1] if stamps else "",f"{stamps[0]}  ->  {stamps[-1]}" if len(stamps)>1 else p.name,p))
 for p in list_restores(d):
  m=RESTORE_RE.match(p.name);rows.append((m["when"],"RESTORE",m["when"],f"restored from {m['stamp']}",p))
 return rows
def version_history():
 w=tk.Toplevel(root);w.title("Backtrail - Version History");w.geometry("1420x760");w.configure(bg=BG)
 top=ttk.Frame(w,padding=(8,8,8,2));top.pack(fill="x")
 ttk.Label(top,text="Source").pack(side="left")
 folders=source_folders(backup_root());names=["All sources"]+[d.name for d in folders];srcv=tk.StringVar(value=names[0])
 cb=ttk.Combobox(top,textvariable=srcv,values=names,state="readonly",width=34);cb.pack(side="left",padx=8)
 ttk.Label(top,text="Every backup: when, by whom, mode, note and signature. COMPARE and RESTORE rows are the reports written by Compare Backups and Restore.",foreground=MUTED).pack(side="left",padx=8)
 pan=ttk.Panedwindow(w,orient="horizontal");pan.pack(fill="both",expand=True,padx=8,pady=(4,8))
 lf=ttk.Frame(pan);rf=ttk.Frame(pan);pan.add(lf,weight=1);pan.add(rf,weight=1)
 cols=("src","when","by","mode","sig","note");tf=ttk.Frame(lf);tf.pack(fill="both",expand=True)
 tv=ttk.Treeview(tf,columns=cols,show="headings",selectmode="browse",style="Compact.Treeview")
 for c,t,wid,anc in (("src","Source",190,"w"),("when","When",150,"w"),("by","By",90,"w"),("mode","Mode",82,"w"),("sig","Signature",112,"w"),("note","Note",260,"w")):
  tv.heading(c,text=t);tv.column(c,width=wid,anchor=anc,stretch=(c=="note"))
 sb=ttk.Scrollbar(tf,orient="vertical",command=tv.yview);tv.configure(yscrollcommand=sb.set)
 tv.pack(side="left",fill="both",expand=True);sb.pack(side="left",fill="y")
 tv.tag_configure("report",foreground=MUTED);tv.tag_configure("bad",foreground=ACCENT2);tv.tag_configure("odd",background=STRIPE)
 tp,txt=textpane(rf);tp.pack(fill="both",expand=True)
 entries={}   # iid -> (path, backup or None)
 def fill(*_):
  for x in tv.get_children():tv.delete(x)
  entries.clear();rows=[]
  for d in folders:
   if srcv.get()!="All sources" and d.name!=srcv.get():continue
   for b in list_backups(d):
    m=b.meta;s=b.signature()["status"]
    rows.append((b.stamp,0,(d.name,b.stamp,b.by(),m.get("mode",""),b.sig_mark()+("" if b.complete else " (incomplete)"),m.get("note","")),"bad" if s in("invalid","untrusted") else "",b.changelog or b.manifest,b))
   for sort,kind,when,note,p in _report_rows(d):rows.append((sort,1,(d.name,when,"",kind,"",note),"report",p,None))
  rows.sort(key=lambda r:(r[0],r[1]),reverse=True)
  for i,(_,_,vals,tag,p,b) in enumerate(rows):
   if p is None:continue
   iid=tv.insert("","end",values=vals,tags=tuple(t for t in (tag,"odd" if i%2 else "") if t));entries[iid]=(p,b)
  txt.delete("1.0","end")
 cb.bind("<<ComboboxSelected>>",fill);fill()
 def current():
  s=tv.selection();return entries.get(s[0]) if s else None
 def show(e=None):
  cur=current()
  if cur:
   txt.delete("1.0","end")
   try:txt.insert("1.0",cur[0].read_text(encoding="utf-8",errors="replace"))
   except OSError as ex:txt.insert("1.0",str(ex))
 tv.bind("<<TreeviewSelect>>",show)
 bar=ttk.Frame(lf);bar.pack(fill="x",pady=(6,0));vst=tk.StringVar()
 def need_backup(what):
  cur=current()
  if not cur:messagebox.showinfo(what,"Pick a backup first.",parent=w);return None
  if cur[1] is None:messagebox.showinfo(what,"A COMPARE / RESTORE report is not a backup: pick a backup row.",parent=w);return None
  return cur[1]
 def verify_selected():
  # the deep check: the archive is re-hashed against the signed value, so it can take a while on big archives
  b=need_backup("Verify")
  if not b:return
  vst.set("Verifying %s (re-hashing the archive)..."%b.stamp)
  def work():
   r=verify_archive(b);mark=SIG_MARKS.get(r["status"],r["status"])
   log(("SUCCESS: " if r["status"]=="signed" else "WARNING: ")+f"{b.folder.name} {b.stamp}: {mark} - {r['detail']}","verify")
   root.after(0,lambda:vst.set(f"{mark}: {r['detail']}"))
  threading.Thread(target=work,daemon=True).start()
 def open_folder():
  cur=current()
  if cur:
   try:os.startfile(str(cur[0].parent))
   except OSError:pass
 ttk.Button(bar,text="Restore...",style="Accent.TButton",command=lambda:(lambda b:b and restore_dialog(b,on_done=fill))(need_backup("Restore"))).pack(side="left")
 ttk.Button(bar,text="Verify archive",command=verify_selected).pack(side="left",padx=(8,0))
 ttk.Button(bar,text="Open folder",command=open_folder).pack(side="left",padx=(8,0))
 ttk.Label(bar,textvariable=vst,foreground=ACCENT,wraplength=380).pack(side="left",padx=10)

def restore_dialog(backup=None,folder=None,preselect=None,on_done=None):
 # put a backup back: every file or a few, into a new folder or over the live source.
 # `preselect` = file names to start with selected (from a changelog / comparison); None = all files.
 folders=source_folders(backup_root())
 if backup is None and folder is None:
  i=selected()
  if i is not None:
   d=backup_root()/safe_name(cfg["locations"][i].get("backup_name") or cfg["locations"][i].get("name") or "")
   if d in folders:folder=d
  folder=folder or (folders[0] if folders else None)
  if folder is None:return messagebox.showinfo("Restore","No backups found under the destination.")
 folder=Path(folder) if folder else backup.folder
 bks=[b for b in reversed(list_backups(folder)) if b.archive]
 if not bks:return messagebox.showinfo("Restore",f"{folder.name}: no backup with an archive to restore from.")
 w=tk.Toplevel(root);w.title(f"Backtrail - Restore {folder.name}");w.geometry("1000x720");w.configure(bg=BG);w.minsize(860,600)
 cur=[backup if backup and backup.archive else bks[0]];busy=[False]
 # --- which backup
 top=ttk.Frame(w,padding=(12,10,12,4));top.pack(fill="x")
 ttk.Label(top,text="Backup",font=("Segoe UI",9,"bold")).pack(side="left")
 labels=[b.label() for b in bks];bv=tk.StringVar(value=cur[0].label())
 cbb=ttk.Combobox(top,textvariable=bv,values=labels,state="readonly",width=90);cbb.pack(side="left",padx=8,fill="x",expand=True)
 # --- the files
 ff=ttk.Frame(w,padding=(12,4));ff.pack(fill="both",expand=True)
 fl=ttk.Frame(ff);fl.pack(fill="x")
 ttk.Label(fl,text="Files").pack(side="left");flt=tk.StringVar();fe=ttk.Entry(fl,textvariable=flt,width=30);fe.pack(side="left",padx=(8,12))
 cnt=tk.StringVar();ttk.Label(fl,textvariable=cnt,foreground=MUTED).pack(side="right")
 tf=ttk.Frame(ff);tf.pack(fill="both",expand=True,pady=(4,0))
 tv=ttk.Treeview(tf,columns=("f","sz","st"),show="headings",selectmode="extended",style="Compact.Treeview")
 for c,t,wid,anc,stre in (("f","File",520,"w",True),("sz","Size",90,"e",False),("st","Compared with the live source",230,"w",False)):
  tv.heading(c,text=t);tv.column(c,width=wid,anchor=anc,stretch=stre)
 sb=ttk.Scrollbar(tf,orient="vertical",command=tv.yview);tv.configure(yscrollcommand=sb.set)
 tv.pack(side="left",fill="both",expand=True);sb.pack(side="left",fill="y")
 tv.tag_configure("differs",foreground=ACCENT2);tv.tag_configure("same",foreground=MUTED)
 state_of={};rows={};last=[""]   # rel -> plan state ; rel -> iid ; the last restore result, shown above the plan
 def status_text(rel):
  s=state_of.get(rel,"")
  return {"replace":"differs (would be replaced)","add":"missing in the source (would be added)","same":"identical"}.get(s,s)
 def fill_files(keep_sel=None):
  sel=set(keep_sel) if keep_sel is not None else {tv.item(i,"values")[0] for i in tv.selection()}
  for x in tv.get_children():tv.delete(x)
  rows.clear();b=cur[0];term=flt.get().strip().lower()
  for rel in sorted(b.files or {}):
   if term and term not in rel.lower():continue
   st=state_of.get(rel,"");iid=tv.insert("","end",values=(rel,f"{b.files[rel].get('size',0):,}",status_text(rel)),tags=(st if st in("same",) else "differs" if st in("replace","add") else "",))
   rows[rel]=iid
  tv.selection_set([rows[r] for r in sel if r in rows]);update_count()
 def update_count(*_):
  n=len(tv.selection());t=len(cur[0].files or {});cnt.set(f"{n} of {t} file(s) selected"+("  -  full restore" if t and n==t else "")+("" if cur[0].files else "  (no manifest: the whole archive is restored, unverified)"))
 tv.bind("<<TreeviewSelect>>",update_count)
 fe.bind("<KeyRelease>",lambda e:fill_files())
 bb=ttk.Frame(ff);bb.pack(fill="x",pady=(6,0))
 def sel_all():tv.selection_set(list(rows.values()));update_count()
 def sel_changed():tv.selection_set([rows[r] for r,s in state_of.items() if s in("replace","add") and r in rows]);update_count()
 def sel_none():tv.selection_set([]);update_count()
 ttk.Button(bb,text="Select all",command=sel_all).pack(side="left");ttk.Button(bb,text="Select changed",command=sel_changed).pack(side="left",padx=6);ttk.Button(bb,text="Clear selection",command=sel_none).pack(side="left")
 # --- where to
 tf=ttk.LabelFrame(w,text=" RESTORE TO ",padding=12);tf.pack(fill="x",padx=12,pady=(6,4))
 mode=tk.StringVar(value="folder");newdir=tk.StringVar();srcp=tk.StringVar();clean=tk.BooleanVar(value=False);note=tk.StringVar()
 ttk.Radiobutton(tf,text="A new folder:",variable=mode,value="folder").grid(row=0,column=0,sticky="w")
 ttk.Entry(tf,textvariable=newdir).grid(row=0,column=1,sticky="ew",padx=8)
 ttk.Button(tf,text="Browse",command=lambda:newdir.set(filedialog.askdirectory(parent=w) or newdir.get())).grid(row=0,column=2)
 rb_src=ttk.Radiobutton(tf,text="Over the live source folder:",variable=mode,value="source");rb_src.grid(row=1,column=0,sticky="w",pady=(8,0))
 ttk.Entry(tf,textvariable=srcp,state="readonly").grid(row=1,column=1,sticky="ew",padx=8,pady=(8,0))
 ttk.Label(tf,text="A signed safety backup of the source is taken first, so what you overwrite is never lost.",foreground=MUTED).grid(row=2,column=1,sticky="w",padx=8)
 ck=ttk.Checkbutton(tf,text="Exact restore: also remove files in the source that this backup does not have (full restore only)",variable=clean);ck.grid(row=3,column=0,columnspan=3,sticky="w",pady=(6,0))
 nr=ttk.Frame(tf);nr.grid(row=4,column=0,columnspan=3,sticky="ew",pady=(6,0))
 ttk.Label(nr,text="Safety backup note").pack(side="left");ttk.Entry(nr,textvariable=note,width=70).pack(side="left",padx=8,fill="x",expand=True)
 tf.columnconfigure(1,weight=1)
 # --- go
 bar=ttk.Frame(w,padding=(12,4,12,12));bar.pack(fill="x");st=tk.StringVar();sigv=tk.StringVar()
 btn_close=ttk.Button(bar,text="Close",command=w.destroy);btn_close.pack(side="right")
 btn_go=ttk.Button(bar,text="▶  RESTORE",style="Accent.TButton");btn_go.pack(side="right",padx=(0,8))   # packed first: keeps its width
 info=ttk.Frame(bar);info.pack(side="left",fill="x",expand=True)
 ttk.Label(info,textvariable=sigv,font=("Segoe UI",9,"bold")).pack(anchor="w")
 ttk.Label(info,textvariable=st,foreground=ACCENT,wraplength=640,justify="left").pack(anchor="w")
 def selected_members():
  rels=[tv.item(i,"values")[0] for i in tv.selection()]
  if not rels:return None,[]
  full=cur[0].files and len(rels)==len(cur[0].files)
  return (None if full else rels),rels
 def load_backup(*_):
  # switch backup: files, states against the source, default folder and note
  b=next((x for x in bks if x.label()==bv.get()),cur[0])
  if b is not cur[0]:last[0]=""
  cur[0]=b;state_of.clear()
  s=b.signature() if b.manifest else {"status":"unsigned","detail":"no manifest"}
  sigv.set(f"{SIG_MARKS.get(s['status'],s['status'])}: {s['detail']}")
  src=source_of(b,cfg);srcp.set(str(src) if src else "(unknown: not configured and not in the manifest)")
  rb_src.state(["!disabled"] if src else ["disabled"])
  if not src and mode.get()=="source":mode.set("folder")
  newdir.set(str(backup_root()/"_restore"/f"{b.stamp}_{b.name}"));note.set(f"safety backup before restore of {b.stamp}")
  init=preselect if (preselect is not None and b is backup) else None
  fill_files(init)
  if init is None:sel_all()
  if src and b.files:
   st.set("Checking the live source...")
   def work():
    try:
     plan=restore_plan(b,src)
     root.after(0,lambda:apply_plan(b,plan))
    except Exception as ex:
     msg=str(ex);root.after(0,lambda:st.set("Could not read the source: "+msg))
   threading.Thread(target=work,daemon=True).start()
  else:st.set("")
 def apply_plan(b,plan):
  if cur[0] is not b:return
  for k in("add","replace","same"):
   for rel in plan[k]:state_of[rel]=k
  fill_files();st.set(last[0]+f"Against the source: {len(plan['replace'])} differ, {len(plan['add'])} missing, {len(plan['same'])} identical, {len(plan['extra'])} extra file(s) in the source.")
 cbb.bind("<<ComboboxSelected>>",load_backup)
 def go():
  if busy[0]:return
  b=cur[0]
  if b.files:
   members,rels=selected_members()
   if not rels:return messagebox.showinfo("Restore","Select at least one file.",parent=w)
  else:members,rels=None,[]   # no manifest: the whole archive
  s=b.signature()["status"] if b.manifest else "unsigned"
  if s in("invalid","untrusted"):
   if not messagebox.askyesno("Signature problem",f"The signature of this backup is {SIG_MARKS.get(s,s)}:\n{b.signature()['detail']}\n\nIts manifest may have been altered. Restore anyway?",icon="warning",parent=w):return
  over=mode.get()=="source";doclean=bool(clean.get()) and members is None and over
  if over:
   if _busy[0]:return messagebox.showinfo("Restore","A backup run is in progress: wait for it to finish.",parent=w)
   src=source_of(b,cfg)
   if not src:return
   n=f"{len(rels)} file(s)" if members is not None else (f"all {len(rels)} files" if rels else "the whole archive")
   extra=""
   if doclean:
    try:extra=f"\n- {len(restore_plan(b,src)['extra'])} file(s) the backup does not have will be REMOVED"
    except Exception:extra="\n- files the backup does not have will be REMOVED"
   if not messagebox.askyesno("Restore over the live source?",f"Restore {n} of\n{b.prefix}\nover\n{src} ?\n\n- A safety backup of the source is taken first (note: \"{note.get().strip()}\")\n- {sum(1 for r in rels if state_of.get(r)=='replace')} file(s) will be replaced, {sum(1 for r in rels if state_of.get(r)=='add')} added, {sum(1 for r in rels if state_of.get(r)=='same')} left as they are{extra}",icon="warning",parent=w):return
   target=src
  else:
   target=Path(newdir.get().strip() or "")
   if not str(target) or str(target)==".":return messagebox.showinfo("Restore","Give a folder to restore into.",parent=w)
   if target.exists() and any(target.iterdir()) and not messagebox.askyesno("Folder not empty",f"{target}\nalready has files. Restored files overwrite files of the same name. Continue?",parent=w):return
  busy[0]=True;st.set("Restoring...")
  if over:_busy[0]=True;_setbusy(True)
  def work():
   try:
    lg=lambda m:log(m,"restore")
    if over:
     out=restore_to_source(b,cfg,members,lg,clean=doclean,note=note.get())
     l=location_for(b,cfg)
     if l:
      key=l.get("backup_name") or l.get("name")
      with _slock:state[key]={"time":datetime.now().isoformat(timespec="seconds"),"changelog":str(b.folder/(out["safety"]+"_CHANGELOG.txt")),"user":identity()[0],"mode":"manual","note":note.get().strip()};savestate()
    else:out=restore(b,target,cfg,members,lg,clean=False)
    msg=f"Restored {len(out['restored'])} file(s)"+(f", {len(out['skipped'])} already identical" if out["skipped"] else "")+(f", {len(out['removed'])} removed" if out["removed"] else "")+(f", {len(out['failed'])} FAILED (see the activity log)" if out["failed"] else "")+f"  ->  {target}"
    for rel,why in out["failed"]:lg(f"ERROR: {rel}: {why}")
    def done():
     last[0]=msg+chr(10);st.set(msg)
     if not out["failed"] and not over:
      if messagebox.askyesno("Restore finished",msg+"\n\nOpen the folder?",parent=w):
       try:os.startfile(str(target))
       except OSError:pass
     elif out["failed"]:messagebox.showwarning("Restore finished with errors",msg,parent=w)
     else:messagebox.showinfo("Restore finished",msg+"\n\nSafety backup: "+out["safety"],parent=w)
     load_backup()
    root.after(0,done)
   except Exception as ex:
    msg=str(ex);log("ERROR: "+msg,"restore")
    root.after(0,lambda:(st.set("Failed: "+msg),messagebox.showerror("Restore failed",msg,parent=w)))
   finally:
    busy[0]=False
    if over:
     _busy[0]=False
     if not _quit[0]:root.after(0,lambda:(_setbusy(False),refresh_sources()))
    if on_done:root.after(0,on_done)
  threading.Thread(target=work,daemon=True).start()
 btn_go.configure(command=go)
 load_backup()

def compare_dialog():
 # the changelog between ANY two backups: two of one source, or two archive files from anywhere
 w=tk.Toplevel(root);w.title("Backtrail - Compare Backups");w.geometry("1220x780");w.configure(bg=BG)
 top=ttk.Frame(w,padding=8);top.pack(fill="x")
 ttk.Label(top,text="Source").pack(side="left")
 folders=source_folders(backup_root());names=[d.name for d in folders];srcv=tk.StringVar(value=names[0] if names else "")
 cb=ttk.Combobox(top,textvariable=srcv,values=names,state="readonly",width=34);cb.pack(side="left",padx=8)
 ttk.Label(top,text="Pick a FROM and a TO backup of the source, or two archive files (.rar / .zip) from anywhere.\nArchives without a manifest beside them are unpacked and hashed first (RAR needs WinRAR, 7-Zip or the tar of Windows 11).",foreground=MUTED).pack(side="left",padx=8)
 mid=ttk.Frame(w,padding=(8,0));mid.pack(fill="x")
 def lbox(parent,title):
  f=ttk.Frame(parent);ttk.Label(f,text=title,font=("Segoe UI",9,"bold")).pack(anchor="w")
  lb=tk.Listbox(f,height=8,bg=CARD,fg=TEXT,selectbackground=ACCENT,selectforeground="#FFFFFF",relief="flat",highlightthickness=1,highlightbackground=BORDER,activestyle="none",font=("Segoe UI",10),exportselection=False);lb.pack(fill="both",expand=True)
  return f,lb
 lf,lb_from=lbox(mid,"FROM (older)");lf.pack(side="left",fill="both",expand=True,padx=(0,4))
 rf,lb_to=lbox(mid,"TO (newer)");rf.pack(side="left",fill="both",expand=True,padx=(4,0))
 bks=[]
 def fill(*_):
  nonlocal bks
  d=next((x for x in folders if x.name==srcv.get()),None)
  bks=list(reversed(list_backups(d))) if d else []
  for lb in (lb_from,lb_to):lb.delete(0,"end")
  for b in bks:
   for lb in (lb_from,lb_to):lb.insert("end",b.label())
  if len(bks)>1:lb_from.selection_set(1);lb_to.selection_set(0)
  elif bks:lb_to.selection_set(0)
 cb.bind("<<ComboboxSelected>>",fill);fill()
 bar=ttk.Frame(w,padding=8);bar.pack(fill="x");st=tk.StringVar()
 vp,viewer=textpane(w);vp.pack(fill="both",expand=True,padx=8,pady=(0,8))
 result=[None];busy=[False];pair=[None,None]
 def show(text):viewer.delete("1.0","end");viewer.insert("1.0",text)
 def go(old,new):
  if busy[0]:return
  busy[0]=True;st.set("Comparing...")
  def work():
   try:
    out,c=compare(old,new,cfg,log=lambda m:log(m,"compare"))
    result[0]=out;pair[:]=sorted((old,new),key=lambda x:x.stamp) if isinstance(old,Backup) and isinstance(new,Backup) else [None,None]
    root.after(0,lambda:(show(out.read_text(encoding="utf-8",errors="replace")),st.set(f"+{c[0]} ~{c[1]} -{c[2]}   written: {out}")))
   except Exception as ex:
    msg=str(ex)   # bound now: `ex` is gone once the except block ends, before the callback runs
    root.after(0,lambda:(st.set("Failed: "+msg),messagebox.showerror("Compare failed",msg,parent=w)))
   finally:busy[0]=False
  threading.Thread(target=work,daemon=True).start()
 def go_sel():
  a,b=lb_from.curselection(),lb_to.curselection()
  if not a or not b:return messagebox.showinfo("Compare","Pick a FROM and a TO backup.",parent=w)
  if a[0]==b[0]:return messagebox.showinfo("Compare","Pick two different backups.",parent=w)
  go(bks[a[0]],bks[b[0]])
 def go_files():
  t=[("Backup archives","*.rar *.zip *.7z"),("All files","*.*")]
  a=filedialog.askopenfilename(title="OLDER backup archive",filetypes=t,parent=w)
  if not a:return
  b=filedialog.askopenfilename(title="NEWER backup archive",filetypes=t,initialdir=str(Path(a).parent),parent=w)
  if not b:return
  go(a,b)
 def open_folder():
  if result[0]:
   try:os.startfile(str(Path(result[0]).parent))
   except OSError:pass
 def restore_from():
  # the files that differ between the two, offered for restore from the FROM (older) backup
  old,new=pair
  if not old:return messagebox.showinfo("Restore","Compare two backups of a source first (archive files picked from anywhere cannot be restored here).",parent=w)
  diff=[r for r in old.files if r not in new.files or new.files[r]["sha256"]!=old.files[r]["sha256"]]
  restore_dialog(old,preselect=diff)
 ttk.Button(bar,text="Compare selected",style="Accent.TButton",command=go_sel).pack(side="left")
 ttk.Button(bar,text="Compare two archive files...",command=go_files).pack(side="left",padx=8)
 ttk.Button(bar,text="Open result folder",command=open_folder).pack(side="left")
 ttk.Button(bar,text="Restore changed files from FROM...",command=restore_from).pack(side="left",padx=8)
 ttk.Label(bar,textvariable=st,foreground=ACCENT).pack(side="left",padx=12)

def search_history():
 w=tk.Toplevel(root);w.title("Backtrail - Search History");w.geometry("1150x700");w.configure(bg=BG)
 top=ttk.Frame(w,padding=8);top.pack(fill="x");q=tk.StringVar()
 ttk.Label(top,text="Search file names, parameters or values:").pack(side="left")
 e=ttk.Entry(top,textvariable=q);e.pack(side="left",fill="x",expand=True,padx=8)
 results=tk.Listbox(w,height=12,bg=CARD,fg=TEXT,selectbackground=ACCENT,selectforeground="#FFFFFF",relief="flat",highlightthickness=1,highlightbackground=BORDER,activestyle="none",font=("Segoe UI",10));results.pack(fill="x",padx=8)
 vp,viewer=textpane(w);vp.pack(fill="both",expand=True,padx=8,pady=8)
 matches=[]
 def go(*_):
  nonlocal matches;matches=[];results.delete(0,"end");term=q.get().strip().lower()
  if not term:return
  for p in changelogs()+compares()+restores():
   t=p.read_text(encoding="utf-8",errors="replace")
   if term in t.lower():
    lines=t.splitlines()
    hits=[f"{i+1}: {line}" for i,line in enumerate(lines) if term in line.lower()]
    matches.append((p,t,hits));results.insert("end",f"{p.parent.name} | {p.name} | {len(hits)} hit(s)")
 def show(_=None):
  if results.curselection():
   p,t,h=matches[results.curselection()[0]]
   viewer.delete("1.0","end");viewer.insert("1.0",f"{p}\n\n"+" \n".join(h)+"\n\n"+"="*90+"\n"+t)
 ttk.Button(top,text="Search",command=go).pack(side="left");e.bind("<Return>",go);results.bind("<<ListboxSelect>>",show);e.focus()

root=tk.Tk();root.title(f"{TOOL} v{VERSION}");root.geometry("1240x900");root.minsize(1000,700)
try:root.iconbitmap(default=str(Path(getattr(sys,"_MEIPASS",str(BASE)))/"Backtrail.ico"))
except Exception:pass

# ===================== visual theme =====================
# teal accent on slate: calm, readable, no brand attached
DARK="#1E293B";DARK2="#334155";ACCENT="#0F766E";ACCENT2="#115E59";BG="#F3F5F7";CARD="#FFFFFF";BORDER="#D9DEE5"
TEXT="#1E293B";MUTED="#64748B";STRIPE="#F6F8FA";CON_BG="#0F172A";CON_FG="#E2E8F0"
root.configure(bg=BG)
try:root.option_add("*Font",("Segoe UI",10))
except:pass
style=ttk.Style();style.theme_use("clam")
style.configure(".",background=BG,foreground=TEXT,font=("Segoe UI",10))
style.configure("TFrame",background=BG)
style.configure("TLabel",background=BG,foreground=TEXT)
style.configure("Head.TLabel",background=DARK,foreground="#FFFFFF")
# --- buttons ---
style.configure("TButton",background="#FFFFFF",foreground=TEXT,bordercolor=BORDER,borderwidth=1,relief="flat",padding=(13,7),font=("Segoe UI",9,"bold"))
style.map("TButton",background=[("active","#E8EEF3"),("pressed","#DCE4EB")],bordercolor=[("active",ACCENT)])
style.configure("Primary.TButton",background=DARK,foreground="#FFFFFF",borderwidth=0,padding=(18,11),font=("Segoe UI",11,"bold"))
style.map("Primary.TButton",background=[("disabled","#6E6F75"),("active",DARK2),("pressed",DARK2)],foreground=[("disabled","#D8D8DC")])
style.configure("Accent.TButton",background=ACCENT,foreground="#FFFFFF",borderwidth=0,padding=(18,11),font=("Segoe UI",11,"bold"))
style.map("Accent.TButton",background=[("disabled","#8FC4BE"),("active",ACCENT2),("pressed",ACCENT2)],foreground=[("disabled","#FFFFFF")])
# --- treeview ---
style.configure("Treeview",background=CARD,fieldbackground=CARD,foreground=TEXT,rowheight=30,borderwidth=0,font=("Segoe UI",10))
style.configure("Treeview.Heading",background=DARK,foreground="#FFFFFF",relief="flat",padding=(8,8),font=("Segoe UI",9,"bold"))
style.map("Treeview.Heading",background=[("active",DARK2)])
style.map("Treeview",background=[("selected",ACCENT)],foreground=[("selected","#FFFFFF")])
style.configure("Compact.Treeview",rowheight=24)   # the dense lists of Version History and Restore
# --- inputs / frames ---
style.configure("TLabelframe",background=BG,bordercolor=BORDER,borderwidth=1,relief="solid")
style.configure("TLabelframe.Label",background=BG,foreground=DARK,font=("Segoe UI",9,"bold"))
style.configure("TEntry",fieldbackground=CARD,bordercolor=BORDER,relief="flat",padding=4)
style.configure("TCombobox",fieldbackground=CARD,bordercolor=BORDER,arrowcolor=DARK,padding=4)
style.map("TCombobox",fieldbackground=[("readonly",CARD)])

# ===================== Header bar =====================
header=tk.Frame(root,bg=DARK);header.pack(fill="x")
hin=tk.Frame(header,bg=DARK);hin.pack(fill="x",padx=22,pady=(16,14))
tk.Label(hin,text=TOOL,bg=DARK,fg="#FFFFFF",font=("Segoe UI",23,"bold")).pack(side="left")
tk.Label(hin,text="  "+TAGLINE.upper(),bg=DARK,fg="#94A3B8",font=("Segoe UI",10,"bold")).pack(side="left",anchor="s",pady=(0,4))
tk.Label(hin,text=f"v{VERSION}",bg=ACCENT,fg="#FFFFFF",font=("Segoe UI",9,"bold"),padx=8,pady=2).pack(side="right",anchor="n")
tk.Frame(root,bg=ACCENT,height=3).pack(fill="x")

# ===================== Dashboard cards =====================
dash=tk.Frame(root,bg=BG);dash.pack(fill="x",padx=16,pady=(14,6));cards={}
for key,title,accent in (("sources","SOURCES",DARK),("ready","ONLINE",DARK),("backups","BACKUPS",DARK),("changes","LATEST CHANGES",ACCENT),("files","FILES INDEXED",DARK)):
 c=tk.Frame(dash,bg=CARD,highlightbackground=BORDER,highlightthickness=1);c.pack(side="left",fill="x",expand=True,padx=5)
 tk.Frame(c,bg=accent,height=3).pack(fill="x")
 tk.Label(c,text=title,bg=CARD,fg=MUTED,font=("Segoe UI",8,"bold")).pack(anchor="w",padx=15,pady=(11,0))
 l=tk.Label(c,text="0",bg=CARD,fg=accent,font=("Segoe UI",25,"bold"));l.pack(anchor="w",padx=15,pady=(0,12));cards[key]=l

# ===================== Sources table =====================
tframe=tk.Frame(root,bg=BORDER,highlightbackground=BORDER,highlightthickness=1);tframe.pack(fill="x",padx=16,pady=6)
tree=ttk.Treeview(tframe,columns=("n","b","p","s","last"),show="headings",height=8)
for c,t,wid,anc in (("n","Name",140,"w"),("b","Backup Name",160,"w"),("p","Folder",440,"w"),("s","Status",90,"center"),("last","Last Backup  ·  by",240,"center")):
 tree.heading(c,text=t);tree.column(c,width=wid,anchor=anc)
tree.tag_configure("even",background=CARD)
tree.tag_configure("odd",background=STRIPE)
tree.tag_configure("offline",background="#FEF2F2",foreground="#B91C1C")
tree.pack(fill="x")
f=ttk.Frame(root);f.pack(fill="x",padx=16,pady=(2,0))
ttk.Button(f,text="+  Add",command=edit).pack(side="left");ttk.Button(f,text="Edit",command=lambda:edit(selected()) if selected() is not None else None).pack(side="left",padx=5);ttk.Button(f,text="Remove",command=remove).pack(side="left");ttk.Button(f,text="Schedule...",command=schedule).pack(side="left",padx=(12,0))
ttk.Button(f,text="Version History",command=version_history).pack(side="right");ttk.Button(f,text="Search History",command=search_history).pack(side="right",padx=5)
ttk.Button(f,text="Compare Backups...",command=compare_dialog).pack(side="right",padx=(0,5))
ttk.Button(f,text="Restore...",command=restore_dialog).pack(side="right",padx=(0,12))

# ===================== Settings =====================
g=ttk.LabelFrame(root,text=" SETTINGS ",padding=12);g.pack(fill="x",padx=16,pady=10);dest=tk.StringVar();fmt=tk.StringVar();comp=tk.StringVar();par=tk.StringVar()
ttk.Label(g,text="Destination").grid(row=0,column=0);ttk.Entry(g,textvariable=dest,state="readonly").grid(row=0,column=1,sticky="ew",padx=8);ttk.Button(g,text="Browse",command=destination).grid(row=0,column=2)
ttk.Label(g,text="Archive").grid(row=0,column=3,padx=(16,4));ttk.Combobox(g,textvariable=fmt,values=("ZIP","RAR"),state="readonly",width=7).grid(row=0,column=4)
ttk.Label(g,text="Compression").grid(row=0,column=5,padx=(16,4));ttk.Combobox(g,textvariable=comp,values=("Fast","Normal","Maximum"),state="readonly",width=10).grid(row=0,column=6)
ttk.Label(g,text="Parallel").grid(row=0,column=7,padx=(16,4));ttk.Combobox(g,textvariable=par,values=("1","2","3","4","6","8"),state="readonly",width=4).grid(row=0,column=8)
ttk.Button(g,text="Save",command=options).grid(row=0,column=9,padx=8);g.columnconfigure(1,weight=1)
# retention: how many backups to keep per source (0 = everything); applied after each run and by "Prune now"
keep_n=tk.StringVar();keep_d=tk.StringVar()
r1=ttk.Frame(g);r1.grid(row=1,column=0,columnspan=10,sticky="w",pady=(10,0))
ttk.Label(r1,text="Retention").pack(side="left");ttk.Label(r1,text="keep the newest").pack(side="left",padx=(26,4))
ttk.Entry(r1,textvariable=keep_n,width=6,justify="center").pack(side="left")
ttk.Label(r1,text="backups   and / or the last").pack(side="left",padx=4)
ttk.Entry(r1,textvariable=keep_d,width=6,justify="center").pack(side="left")
ttk.Label(r1,text="days   (0 = keep everything; the newest complete backup is never removed)",foreground=MUTED).pack(side="left",padx=(4,12))
ttk.Button(r1,text="Prune now",command=prune_now).pack(side="left")

# ===================== Actions =====================
a=ttk.Frame(root);a.pack(fill="x",padx=16,pady=4)
# the note travels with the backup: manifest, changelog header, Version History ("why was this taken?")
note_var=tk.StringVar()
ttk.Label(a,text="Note").pack(side="left");ttk.Entry(a,textvariable=note_var,width=44).pack(side="left",padx=(6,12))
btn_all=ttk.Button(a,text="▶  RUN ALL BACKUPS NOW",style="Accent.TButton",command=lambda:execute(False));btn_all.pack(side="left",fill="x",expand=True)
btn_sel=ttk.Button(a,text="RUN SELECTED",style="Primary.TButton",command=lambda:execute(True));btn_sel.pack(side="left",padx=(8,0))

# ===================== Console =====================
cframe=tk.Frame(root,bg=CON_BG);cframe.pack(fill="both",expand=True,padx=16,pady=(6,14))
tk.Label(cframe,text="ACTIVITY LOG",bg=CON_BG,fg="#94A3B8",font=("Segoe UI",8,"bold")).pack(anchor="w",padx=10,pady=(7,0))
console=scrolledtext.ScrolledText(cframe,height=9,font=("Consolas",9),bg=CON_BG,fg=CON_FG,insertbackground=CON_FG,relief="flat",borderwidth=0,highlightthickness=0,padx=8,pady=6)
console.pack(fill="both",expand=True)
console.tag_configure("ts",foreground="#94A3B8")
console.tag_configure("src",foreground="#5EEAD4")
console.tag_configure("info",foreground=CON_FG)
console.tag_configure("ok",foreground="#4ADE80")
console.tag_configure("warn",foreground="#FBBF24")
console.tag_configure("err",foreground="#F87171")
root.protocol("WM_DELETE_WINDOW",on_close)
root.after(0,refresh_sources);log(f"{TOOL} v{VERSION} ready - backups are taken as {signer_id()}.")
def _announce_signing():
 if cfg.get("sign",True) is False:return log("WARNING: signing is switched off in config.json - backups will be unsigned.")
 if not signing_available():return log("WARNING: the cryptography package is not installed - backups will be unsigned.")
 try:
  k=load_or_create_key(cfg,log);log(f"Signing: ed25519 key {key_fingerprint(_raw_pub(k))} for {signer_id()} (keys in {keys_dir(cfg)}).")
 except Exception as ex:log("WARNING: signing key unavailable: "+str(ex))
root.after(50,_announce_signing)   # deferred: its status thread must only call root.after() once mainloop() is running
root.mainloop()
