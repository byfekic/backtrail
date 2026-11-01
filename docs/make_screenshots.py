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


base = {"config/machine.json": json.dumps({"machine": "Line 1", "speed": 1.0, "recirculation": 10, "maxItems": 1200}, indent=2),
        "config/network.xml": "<network>\n  <timeout>100</timeout>\n  <mode>run</mode>\n</network>\n",
        "config/version.txt": "6.2.0\nbuild 2026-09-14\n",
        "io/io_map.csv": "signal;board;channel;type\nBELT_RUN;1;5;DI\nGATE_OPEN;1;6;DO\n",
        "recipes/recipe_A.csv": "step;speed;dwell\n1;2800;5\n2;2500;8\n",
        "recipes/speeds.csv": "zone;speed\n1;2800\n2;2500\n",
        "hmi/settings.json": json.dumps({"language": "en", "speed": 1.0, "windows": 10}, indent=2),
        "settings.json": json.dumps({"line": 1, "machine": {"speed": 1.0, "windows": 10}}, indent=2)}
srcs = [("Line 1 PLC", "LINE1_PLC", "Line1"),
        ("Line 1 HMI", "LINE1_HMI", "Line1_HMI"),
        ("Line 2 PLC", "LINE2_PLC", "Line2")]
locs = []
for name, bname, folder in srcs:
    mk(plant / folder, base); locs.append({"name": name, "backup_name": bname, "path": str(plant / folder)})
cfg = {"backup_root": str(bk), "archive_format": "zip", "rar_path": r"C:\Program Files\WinRAR\Rar.exe", "compress_level": 1,
       "parallel_backups": 3, "keep_last": 30, "keep_days": 0, "schedule_time": "20:00", "locations": locs}
quiet = lambda s: None  # noqa: E731
for l in locs: e.run(l, cfg, quiet, note="baseline after commissioning", mode="manual")
time.sleep(1.1)
for name, bname, folder in srcs:
    p = plant / folder / "hmi/settings.json"; d = json.loads(p.read_text()); d["speed"] = 1.5; d["windows"] = 12; p.write_text(json.dumps(d, indent=2))
    (plant / folder / "config/network.xml").write_text("<network>\n  <timeout>250</timeout>\n  <mode>run</mode>\n  <debug>1</debug>\n</network>\n")
    (plant / folder / "io/io_map.csv").write_text("signal;board;channel;type\nBELT_RUN;1;5;DI\nGATE_OPEN;1;6;DO\nGATE_2_OPEN;1;7;DO\n")
for l in locs: e.run(l, cfg, quiet, note="", mode="scheduled")
time.sleep(1.1)
for name, bname, folder in srcs:
    (plant / folder / "recipes/speeds.csv").write_text("zone;speed\n1;2800\n2;2600\n3;2000\n")
    (plant / folder / "config/version.txt").write_text("6.2.1\nbuild 2026-10-02\n")
    (plant / folder / "recipes/recipe_A.csv").unlink()
for l in locs: e.run(l, cfg, quiet, note="after PLC firmware 6.2.1", mode="manual")
(app / "config.json").write_text(json.dumps(cfg, indent=2)); (app / "state.json").write_text("{}")
print("history ready:", [b.label().encode("ascii", "replace").decode() for b in e.list_backups(bk / "LINE1_PLC")])

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
bad=S/"PLANT"/"Line1"/"recipes"/"speeds.csv"
def break_plant(): bad.write_text("zone;speed\n1;9999\n"); (S/"PLANT"/"Line1"/"debug.tmp").write_text("x")
step(break_plant, 300)
step(lambda: version_history(), 900)
def pick_hist(n):
    t=widgets(tops()[-1],ttk.Treeview)[0]; kids=t.get_children()
    if kids: t.selection_set(kids[n]); t.focus(kids[n]); t.event_generate("<<TreeviewSelect>>")
step(lambda: pick_hist(2), 500)
step(lambda: shot_top("04_version_history.png"), 300)
def filter_msc():
    cb=widgets(tops()[-1],ttk.Combobox)[0]; cb.set("LINE1_PLC"); cb.event_generate("<<ComboboxSelected>>")
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
    print("restored speeds.csv:", (S / "PLANT" / "Line1" / "recipes" / "speeds.csv").read_text())
    print("debug.tmp exists:", (S / "PLANT" / "Line1" / "debug.tmp").exists())
    print("Line 1 history:", [b.label().encode("ascii", "replace").decode() for b in e.list_backups(bk / "LINE1_PLC")])
    print("restores:", [p.name for p in e.list_restores(bk / "LINE1_PLC")])
    if letter:
        subprocess.run(["subst", letter + ":", "/d"], capture_output=True)
        print("drive", letter + ":", "released")
