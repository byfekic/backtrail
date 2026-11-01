# Maintainers only: regenerate docs/screenshots.
#   python docs\make_screenshots.py  D:\_ptshots
# Builds a believable demo plant on a short virtual drive (subst), takes a few backups with
# notes, breaks a file, then drives the GUI (no clicking needed) and photographs every window
# with PrintWindow into docs/screenshots. Needs Pillow. The folder given is wiped first.
import sys, os, io, json, time, shutil, ctypes, subprocess, string
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
REAL = Path(sys.argv[1])                      # a short real folder; mapped to a drive letter below
OUT = PROJ / "docs" / "screenshots"
shutil.rmtree(str(REAL), ignore_errors=True); REAL.mkdir(parents=True); OUT.mkdir(parents=True, exist_ok=True)

# a virtual drive keeps every path short and makes the screenshots read like a network drive
letter = None
used = {d for d in string.ascii_uppercase if Path(d + ":\\").exists()}
for d in "PQRSTUVWXYZ":
    if d not in used and subprocess.run(["subst", d + ":", str(REAL)], capture_output=True).returncode == 0:
        letter = d; break
S = Path(letter + ":\\") if letter else REAL
print("working in", S)
app = S / "Backtrail"; plant = S / "PLANT"; bk = S / "Backups"
app.mkdir(); shutil.copy(str(PROJ / "backup_engine.py"), str(app)); shutil.copy(str(PROJ / "Backtrail.ico"), str(app))
ctypes.windll.user32.SetProcessDPIAware()
sys.path.insert(0, str(app)); import backup_engine as e  # noqa: E402


def mk(root, files):
    for rel, txt in files.items():
        p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(txt, encoding="utf-8")


base = {"Applications/PlantConfig.xml": "<PlantConfig>\n  <Line>1</Line>\n  <Timeout>100</Timeout>\n</PlantConfig>\n",
        "Applications/Version.csv": "# Plant Version file\n6.2.0\n# Description: v6.2.0-16-g7b480228\n",
        "IODriver/BusField.csv": "#;Board;Node;Type\n@;1;5;0x1;;;;;;;;;#;ET200SP\n@;1;6;0x1;;;;;;;;;#;Coupler six\n",
        "IODriver/IODriverConfig.xml": "<cfg>\n  <timeout>100</timeout>\n  <mode>run</mode>\n</cfg>\n",
        "MSC/DB_Csv/Plant_Inputs.csv": "#;Signal ID;Type;Logic;...\n@;CHUTE_0201_FULL;1;0;0;0;0;0;0;0;0;5;0;0;0;1;Chute 201 full;\n",
        "MSC/DB_Csv/Plant_Outputs.csv": "#;Output Name;...\n@;CHUTE_0201_OPEN;2;0;5;2;0;0;1;Chute 201 open\n",
        "MSC/DB_Csv/Speeds.csv": "@;1;2800;#\n@;2;2500;#\n",
        "DataDrawSupervisor/DataDraw_PlantData/settings.json": json.dumps({"machine": "Sorter_A", "speed": 1.0, "recirculation": 10, "maxItems": 1200}, indent=2),
        "settings.json": json.dumps({"plant": "Demo", "line": 1, "sorter": {"speed": 1.0, "windows": 10}}, indent=2)}
srcs = [("Line 1 Sorter", "LINE1_SORTER", "Sorter"),
        ("Line 1 Upstream PLC", "LINE1_UPSTREAM_PLC", "Upstream"),
        ("Maintenance PC", "MAINT_PC", "Maint")]
locs = []
for name, bname, folder in srcs:
    mk(plant / folder / "PRODUCTION", base); locs.append({"name": name, "backup_name": bname, "path": str(plant / folder / "PRODUCTION")})
cfg = {"backup_root": str(bk), "archive_format": "zip", "rar_path": r"C:\Program Files\WinRAR\Rar.exe", "compress_level": 1,
       "parallel_backups": 3, "keep_last": 30, "keep_days": 0, "schedule_time": "20:00", "locations": locs}
quiet = lambda s: None  # noqa: E731
for l in locs: e.run(l, cfg, quiet, note="baseline after commissioning", mode="manual")
time.sleep(1.1)
for name, bname, folder in srcs:
    p = plant / folder / "PRODUCTION" / "DataDrawSupervisor/DataDraw_PlantData/settings.json"; d = json.loads(p.read_text()); d["speed"] = 1.5; d["windows"] = 12; p.write_text(json.dumps(d, indent=2))
    (plant / folder / "PRODUCTION" / "IODriver/IODriverConfig.xml").write_text("<cfg>\n  <timeout>250</timeout>\n  <mode>run</mode>\n  <debug>1</debug>\n</cfg>\n")
    (plant / folder / "PRODUCTION" / "MSC/DB_Csv/Plant_Inputs.csv").write_text("#;Signal ID;Type;Logic;...\n@;CHUTE_0201_FULL;1;0;0;0;0;0;0;0;0;5;0;0;0;1;Chute 201 full;\n@;CHUTE_0202_FULL;1;0;0;0;0;0;0;0;0;5;0;1;0;1;Chute 202 full;\n")
for l in locs: e.run(l, cfg, quiet, note="", mode="scheduled")
time.sleep(1.1)
for name, bname, folder in srcs:
    (plant / folder / "PRODUCTION" / "MSC/DB_Csv/Speeds.csv").write_text("@;1;2800;#\n@;2;2600;#\n@;3;2000;#\n")
    (plant / folder / "PRODUCTION" / "Applications/Version.csv").write_text("# Plant Version file\n6.2.1\n# Description: v6.2.1-3-gabcdef12\n")
    (plant / folder / "PRODUCTION" / "MSC/DB_Csv/Plant_Outputs.csv").unlink()
for l in locs: e.run(l, cfg, quiet, note="after sorter firmware 6.2.1", mode="manual")
(app / "config.json").write_text(json.dumps(cfg, indent=2)); (app / "state.json").write_text("{}")
print("history ready:", [b.label().encode("ascii", "replace").decode() for b in e.list_backups(bk / "LINE1_SORTER")])

src = io.open(str(PROJ / "backtrail.py"), encoding="utf-8").read()
TAIL = r'''
import ctypes, ctypes.wintypes as wt, time
OUT=Path(r"__OUT__")
from PIL import Image
class BMIH(ctypes.Structure):
    _fields_=[("biSize",wt.DWORD),("biWidth",wt.LONG),("biHeight",wt.LONG),("biPlanes",wt.WORD),("biBitCount",wt.WORD),
              ("biCompression",wt.DWORD),("biSizeImage",wt.DWORD),("biXPelsPerMeter",wt.LONG),("biYPelsPerMeter",wt.LONG),
              ("biClrUsed",wt.DWORD),("biClrImportant",wt.DWORD)]
def shot(win,name):
    # render the window itself (PrintWindow): nothing that floats above it on screen can get into the picture
    win.update_idletasks(); win.lift(); win.update(); time.sleep(0.4)
    u=ctypes.windll.user32; g=ctypes.windll.gdi32
    hwnd=u.GetParent(win.winfo_id()) or win.winfo_id()
    wr=wt.RECT(); u.GetWindowRect(hwnd,ctypes.byref(wr)); W=wr.right-wr.left; H=wr.bottom-wr.top
    r=wt.RECT(); ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd,9,ctypes.byref(r),ctypes.sizeof(r))
    hdc=u.GetWindowDC(hwnd); mdc=g.CreateCompatibleDC(hdc); bmp=g.CreateCompatibleBitmap(hdc,W,H); g.SelectObject(mdc,bmp)
    u.PrintWindow(hwnd,mdc,2)            # PW_RENDERFULLCONTENT
    bi=BMIH(); bi.biSize=ctypes.sizeof(bi); bi.biWidth=W; bi.biHeight=-H; bi.biPlanes=1; bi.biBitCount=32
    buf=ctypes.create_string_buffer(W*H*4); g.GetDIBits(mdc,bmp,0,H,buf,ctypes.byref(bi),0)
    img=Image.frombuffer("RGB",(W,H),buf,"raw","BGRX",0,1).crop((r.left-wr.left,r.top-wr.top,r.right-wr.left,r.bottom-wr.top))
    g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(hwnd,hdc)
    img.save(str(OUT/name)); print("shot",name,img.size)
def widgets(win,cls=None):
    out=[]
    def rec(w):
        for c in w.winfo_children():
            if cls is None or isinstance(c,cls): out.append(c)
            rec(c)
    rec(win); return out
def tops(): return [x for x in root.winfo_children() if isinstance(x,tk.Toplevel)]
steps=[]
def step(fn,delay=400): steps.append((fn,delay))
def go():
    if not steps: root.after(300,root.destroy); return
    fn,delay=steps.pop(0)
    try: fn()
    except Exception: import traceback; traceback.print_exc()
    root.after(delay,go)
root.geometry("1240x900+40+10")
note_var.set("before firmware update 6.2.1")
step(lambda: None, 2500)
step(lambda: execute(False), 300)
def wait_done():
    if _busy[0]: steps.insert(0,(wait_done,300))
step(wait_done, 300)
step(lambda: None, 2000)
step(lambda: shot(root,"01_main_window.png"), 300)
step(lambda: edit(0), 700)
def shot_top(name):
    if tops(): shot(tops()[-1],name)
def close_top():
    if tops(): tops()[-1].destroy()
step(lambda: shot_top("02_source_dialog.png"), 300); step(close_top, 300)
step(lambda: schedule(), 900)
step(lambda: shot_top("03_schedule_dialog.png"), 300); step(close_top, 300)
step(lambda: compare_dialog(), 900)
def pick_compare():
    w=tops()[-1]; lbs=widgets(w,tk.Listbox)
    lbs[0].selection_clear(0,"end"); lbs[0].selection_set(3); lbs[1].selection_clear(0,"end"); lbs[1].selection_set(1)
    for b in widgets(w,ttk.Button):
        if b.cget("text")=="Compare selected": b.invoke()
step(pick_compare, 3500)
step(lambda: shot_top("05_compare_backups.png"), 300); step(close_top, 300)
step(lambda: search_history(), 900)
def do_search():
    w=tops()[-1]; ents=widgets(w,ttk.Entry); ents[0].insert(0,"speed"); ents[0].event_generate("<Return>")
def pick_hit():
    lbs=widgets(tops()[-1],tk.Listbox)
    if lbs and lbs[0].size(): lbs[0].selection_set(0); lbs[0].event_generate("<<ListboxSelect>>")
step(do_search, 900); step(pick_hit, 500)
step(lambda: shot_top("06_search_history.png"), 300); step(close_top, 300)
# --- restore: a file goes wrong on the plant, the older backup is put back over the source
bad=S/"PLANT"/"Sorter"/"PRODUCTION"/"MSC"/"DB_Csv"/"Speeds.csv"
def break_plant(): bad.write_text("@;1;9999;#\n"); (S/"PLANT"/"Sorter"/"PRODUCTION"/"debug.tmp").write_text("x")
step(break_plant, 300)
step(lambda: version_history(), 900)
def pick_hist(n):
    t=widgets(tops()[-1],ttk.Treeview)[0]; kids=t.get_children()
    if kids: t.selection_set(kids[n]); t.focus(kids[n]); t.event_generate("<<TreeviewSelect>>")
step(lambda: pick_hist(2), 500)
step(lambda: shot_top("04_version_history.png"), 300)
def filter_msc():
    cb=widgets(tops()[-1],ttk.Combobox)[0]; cb.set("LINE1_SORTER"); cb.event_generate("<<ComboboxSelected>>")
step(filter_msc, 400); step(lambda: pick_hist(0), 400)
def click(text):
    for b in widgets(tops()[-1],ttk.Button):
        if b.cget("text")==text: b.invoke(); return
    print("no button",text)
step(lambda: click("Restore..."), 1800)
def set_source_mode():
    w=tops()[-1]
    for r in widgets(w,ttk.Radiobutton):
        if "source" in r.cget("text"): r.invoke()
    for c in widgets(w,ttk.Checkbutton): c.invoke()
step(set_source_mode, 400)
step(lambda: click("Select changed"), 400)
step(lambda: shot_top("07_restore_dialog.png"), 300)
messagebox.askyesno=lambda *a,**k: True
messagebox.showinfo=lambda *a,**k: print("info:",a[1] if len(a)>1 else a)
step(lambda: click("▶  RESTORE"), 400)
def wait_restore():
    if _busy[0]: steps.insert(0,(wait_restore,300))
step(wait_restore, 300); step(lambda: None, 1500)
step(lambda: shot_top("08_restore_done.png"), 300); step(close_top, 300)
step(lambda: None, 400)
step(lambda: pick_hist(0), 500)
step(lambda: shot_top("09_restore_report.png"), 300); step(close_top, 300)
step(lambda: shot(root,"10_main_after_restore.png"), 300)
root.after(400,go); root.mainloop()
print("gui done")
'''.replace("__OUT__", str(OUT))
S_ = S
assert src.count("root.mainloop()") == 1
code = src.replace("root.mainloop()", TAIL)
sys.argv = [str(app / "backtrail.py")]; os.chdir(str(app))
try:
    exec(compile(code, str(app / "backtrail.py"), "exec"), {"__file__": str(app / "backtrail.py"), "__name__": "__main__", "S": S})
finally:
    os.chdir(str(PROJ))
    print("restored Speeds.csv:", (S / "PLANT" / "Sorter" / "PRODUCTION" / "MSC" / "DB_Csv" / "Speeds.csv").read_text())
    print("debug.tmp exists:", (S / "PLANT" / "Sorter" / "PRODUCTION" / "debug.tmp").exists())
    print("MSC history:", [b.label().encode("ascii", "replace").decode() for b in e.list_backups(bk / "LINE1_SORTER")])
    print("restores:", [p.name for p in e.list_restores(bk / "LINE1_SORTER")])
    if letter:
        subprocess.run(["subst", letter + ":", "/d"], capture_output=True)
        print("drive", letter + ":", "released")
