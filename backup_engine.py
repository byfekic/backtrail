import json, hashlib, zipfile, subprocess, shutil, os, re, sys, time, threading, tempfile
import concurrent.futures as cf
from pathlib import Path
from datetime import datetime
NOWIN=getattr(subprocess,"CREATE_NO_WINDOW",0)   # stop Rar.exe flashing a console window when launched from the windowed exe
CANCEL=threading.Event()   # set by the GUI to abort running backups (user quits while a run is in progress)
VERSION="1.0"
TOOL="Backtrail";TAGLINE="Signed backups with a readable history"
MANIFEST_VERSION=2         # v2 = {"_meta": {...}, "files": {...}}; v1 (the original tool) = the bare file map
def _abort(ap,temp):
 # remove the partial archive of an interrupted run; retry briefly in case the writer or AV still holds it
 for _ in range(15):
  try:
   if ap.exists():ap.unlink()
   break
  except OSError:time.sleep(0.2)
 shutil.rmtree(temp,ignore_errors=True)
def nworkers(cfg=None):
 n=int((cfg or {}).get("workers") or 0)
 return max(1,n) if n>0 else max(4,min(32,(os.cpu_count() or 4)*2))
# ---------------------------------------------------------------- who ----
def identity():
 # (user, host): who is running Backtrail. WinRAR stores no author and the scheduled
 # task always runs as the same account, so this is the only place a name survives.
 try:
  import getpass;u=getpass.getuser()
 except Exception:u=os.environ.get("USERNAME") or os.environ.get("USER") or ""
 try:
  import socket;h=socket.gethostname()
 except Exception:h=os.environ.get("COMPUTERNAME") or ""
 return u or "?",h or "?"
def who():return "%s @ %s"%identity()
def safe_name(name):
 # a backup name as it appears in file and folder names
 return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
def cli_note(argv):
 # the note given on the command line: --note TEXT or --note=TEXT ('' when absent)
 for i,a in enumerate(argv):
  if a.lower().startswith("--note="):return a[7:].strip()
  if a.lower()=="--note" and i+1<len(argv):return argv[i+1].strip()
 return ""
# ----------------------------------------------------------- hashing ----
def hash_file(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""): h.update(b)
 return h.hexdigest()
def walk(base):
 out=[]
 try:
  for p in Path(base).rglob("*"):
   try:
    if p.is_file():out.append(p)
   except OSError:pass
 except OSError:pass
 return out
def scan(folder,skipped=None,cfg=None):
 # hashes every file in parallel: file reads and hashlib both release the GIL,
 # so threads give a real speed-up (and hide latency on network paths).
 base=Path(folder);files=walk(base);r={};lk=threading.Lock()
 def one(p):
  if CANCEL.is_set():return
  rel=p.relative_to(base).as_posix()
  try:s=p.stat();d={"size":s.st_size,"mtime":s.st_mtime,"sha256":hash_file(p)}
  except OSError:
   with lk:
    if skipped is not None and rel not in skipped:skipped.append(rel)
   return
  with lk:r[rel]=d
 if files:
  with cf.ThreadPoolExecutor(max_workers=nworkers(cfg)) as ex:
   for _ in ex.map(one,files):pass
 return r
def jsondiff(a,b):
 try:x=json.loads(Path(a).read_text(encoding="utf-8-sig"));y=json.loads(Path(b).read_text(encoding="utf-8-sig"))
 except:return []
 out=[]
 def w(x,y,p="$"):
  if isinstance(x,dict) and isinstance(y,dict):
   for k in sorted(x.keys()-y.keys()):out.append(f"- {p}.{k}: {x[k]!r}")
   for k in sorted(y.keys()-x.keys()):out.append(f"+ {p}.{k}: {y[k]!r}")
   for k in sorted(x.keys()&y.keys()):w(x[k],y[k],f"{p}.{k}")
  elif x!=y:out.append(f"~ {p}: {x!r} -> {y!r}")
 w(x,y);return out
# --------------------------------------------------------- manifests ----
def load_manifest(p):
 # -> (meta, files). Reads both the v2 wrapper and the bare v1 map of the original tool.
 d=json.loads(Path(p).read_text(encoding="utf-8"))
 if isinstance(d,dict) and isinstance(d.get("_meta"),dict) and isinstance(d.get("files"),dict) and "sha256" not in d["files"]:
  return d["_meta"],d["files"]
 return {},d
def save_manifest(p,files,meta):
 Path(p).write_text(json.dumps({"_meta":meta,"files":files},indent=2),encoding="utf-8")
def summary_counts(changelog):
 # the Added / Modified / Deleted counts of a changelog's SUMMARY section
 try:t=Path(changelog).read_text(encoding="utf-8",errors="replace")
 except OSError:return {"added":0,"modified":0,"deleted":0}
 def n(k):
  m=re.search(rf"^{k}:\s*(\d+)",t,re.M);return int(m.group(1)) if m else 0
 return {"added":n("Added"),"modified":n("Modified"),"deleted":n("Deleted")}
def header_meta(changelog):
 # By / Mode / Note / Date / Source from a changelog header -- the only record
 # for backups whose manifest (v1) carries no metadata
 meta={}
 try:
  for line in Path(changelog).read_text(encoding="utf-8",errors="replace").splitlines()[:20]:
   k,_,v=line.partition(":");k=k.strip().lower();v=v.strip()
   if k=="by":
    u,_,h=v.partition("@");meta["user"]=u.strip();meta["host"]=h.strip()
   elif k in("mode","note","date","source","name","previous"):meta[k]=v
 except OSError:pass
 return meta
# ----------------------------------------------------------- backups ----
STAMP_RE=re.compile(r"^(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(?P<name>.+?)_(?P<kind>PRODUCTION\.(?:zip|rar)|MANIFEST\.json|CHANGELOG\.txt)$",re.I)
def parse_stamp(text):
 # datetime from a backup or archive name: 2026-09-07_16-47-49_... (Backtrail) or
 # LINE1_MSC_20260907_1647.rar (a plant's own nightly batch); None when it has none
 m=re.search(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:-(\d{2}))?",str(text))
 if not m:m=re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})?(?!\d)",str(text))
 if not m:return None
 g=m.groups()
 try:return datetime(int(g[0]),int(g[1]),int(g[2]),int(g[3]),int(g[4]),int(g[5] or 0))
 except ValueError:return None
class Backup:
 # one backup of a source: its archive, manifest and changelog, found by their common prefix
 def __init__(self,folder,stamp,name):
  self.folder=Path(folder);self.stamp=stamp;self.name=name
  self.archive=self.manifest=self.changelog=None;self._meta=None;self._files=None;self._sig=None
 @property
 def prefix(self):return f"{self.stamp}_{self.name}"
 def signature(self):
  # the quick signature check, cached
  if self._sig is None:
   self._sig=verify_manifest(self.manifest) if self.manifest else {"status":"unsigned","detail":"no manifest","signer":"","key_id":""}
  return self._sig
 def sig_mark(self):return SIG_MARKS.get(self.signature()["status"],"")
 @property
 def complete(self):return bool(self.archive and self.manifest)
 @property
 def time(self):
  t=parse_stamp(self.stamp)
  if t:return t
  p=self.manifest or self.archive or self.changelog
  return datetime.fromtimestamp(p.stat().st_mtime) if p else datetime.now()
 def load(self):
  if self._files is None:
   if self.manifest:
    try:self._meta,self._files=load_manifest(self.manifest)
    except (OSError,ValueError):self._meta,self._files={},{}
   else:self._meta,self._files={},{}
   if not self._meta.get("user") and self.changelog:
    self._meta={**header_meta(self.changelog),**self._meta}
  return self._meta,self._files
 @property
 def meta(self):return self.load()[0]
 @property
 def files(self):return self.load()[1]
 def by(self):return self.meta.get("user") or "(unknown)"
 def label(self):
  m=self.meta;s=f"{self.stamp}  by {self.by()}"
  if m.get("mode"):s+=f"  [{m['mode']}]"
  s+="  "+self.sig_mark()            # before the note, which may be long
  if not self.complete:s+="  (incomplete)"
  if m.get("note"):s+=f"  - {m['note']}"
  return s
 def paths(self):return [p for p in (self.archive,self.manifest,self.changelog) if p]
 def __repr__(self):return f"<Backup {self.prefix}>"
def list_backups(folder):
 # every backup in a source folder, oldest first
 out={};folder=Path(folder)
 if not folder.is_dir():return []
 for p in folder.iterdir():
  m=STAMP_RE.match(p.name)
  if not m or not p.is_file():continue
  b=out.setdefault((m["stamp"],m["name"]),Backup(folder,m["stamp"],m["name"]))
  k=m["kind"].lower()
  if k.startswith("production"):b.archive=p
  elif k.startswith("manifest"):b.manifest=p
  else:b.changelog=p
 return [out[k] for k in sorted(out)]
def source_folders(root):
 # the per-source sub-folders of the backup root that hold at least one backup
 root=Path(root)
 if not root.is_dir():return []
 return [d for d in sorted(root.iterdir()) if d.is_dir() and list_backups(d)]
# ------------------------------------------------------- the changelog ----
def changelog_lines(title,head,old,new,jdiff=None,skipped=(),between=(),arch_err=None):
 # the report both a backup run and a comparison write: header, what happened in
 # between (comparison only), added / deleted / modified with the JSON key changes, summary
 add=sorted(new.keys()-old.keys());delete=sorted(old.keys()-new.keys())
 mod=sorted(k for k in new.keys()&old.keys() if new[k]["sha256"]!=old[k]["sha256"])
 L=[TOOL,TAGLINE,"="*76,title]+[f"{k}: {v}" for k,v in head if v not in (None,"")]+["="*76,""]
 if between:L+=["BACKUP RUNS IN BETWEEN (after FROM, up to and including TO)","-"*76]+list(between)+[""]
 L+=["ADDED","-"*76]+([f"+ {x}" for x in add] or ["(none)"])+["","DELETED","-"*76]+([f"- {x}" for x in delete] or ["(none)"])+["","MODIFIED","-"*76]
 if not mod:L+=["(none)"]
 for rel in mod:
  L+=["",f"* {rel}",f"  OLD: {old[rel]['size']} bytes | {old[rel]['sha256']}",f"  NEW: {new[rel]['size']} bytes | {new[rel]['sha256']}"]
  j=(jdiff or {}).get(rel)
  if j:L+=["  CONFIG (JSON) CHANGES:"]+["    "+x for x in j]
 L+=["","SUMMARY","-"*76,f"Added: {len(add)}",f"Modified: {len(mod)}",f"Deleted: {len(delete)}",f"Unchanged: {len(new)-len(add)-len(mod)}"]
 if arch_err:L+=["","ARCHIVE ERROR","-"*76,arch_err]
 if skipped:L+=["","NOT BACKED UP (FILE IN USE / UNREADABLE)","-"*76]+[f"! {x}" for x in skipped]+[f"Skipped: {len(skipped)}"]
 return L,add,mod,delete
# ---------------------------------------------------------- archives ----
def rar(cfg):
 for p in (cfg.get("rar_path"),r"C:\Program Files\WinRAR\Rar.exe",r"C:\Program Files (x86)\WinRAR\Rar.exe",shutil.which("rar")):
  if p and Path(p).exists():return str(p)
def _tar_libarchive(exe):
 try:r=subprocess.run([exe,"--version"],capture_output=True,text=True,errors="replace",timeout=20,creationflags=NOWIN)
 except Exception:return False
 return "libarchive" in (r.stdout+r.stderr).lower()
def unpack_tool(cfg,ext):
 # (kind, exe) able to unpack this archive type. rar: WinRAR's Rar.exe / UnRAR.exe, 7-Zip,
 # or the tar.exe Windows 11 ships (bsdtar on libarchive, which reads rar and 7z).
 if ext==".zip":return "zip",None
 pf=[os.environ.get(v) for v in ("ProgramFiles","ProgramFiles(x86)","ProgramW6432")]
 pf=[p for p in pf if p]
 cands=[]
 if ext==".rar":
  r=rar(cfg)
  if r:cands.append(("rar",r))
  cands+=[("unrar",p) for p in [shutil.which("unrar")]+[str(Path(b)/"WinRAR"/"UnRAR.exe") for b in pf]]
 cands+=[("7z",p) for p in [shutil.which("7z")]+[str(Path(b)/"7-Zip"/"7z.exe") for b in pf]]
 sysroot=os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
 cands+=[("tar",p) for p in [str(Path(sysroot)/"System32"/"tar.exe"),shutil.which("tar")]]
 for kind,exe in cands:
  if exe and Path(exe).is_file() and (kind!="tar" or _tar_libarchive(exe)):return kind,exe
 return None
def extract_all(a,d,cfg,log=None):
 # unpack a whole archive into d (used when a backup has no manifest beside it)
 a=Path(a);d=Path(d);d.mkdir(parents=True,exist_ok=True)
 tool=unpack_tool(cfg,a.suffix.lower())
 if not tool:raise RuntimeError(f"No program found to open {a.name}: install WinRAR (Rar.exe / UnRAR.exe) or 7-Zip, or set rar_path in config.json.")
 kind,exe=tool
 if log:log(f"Unpacking {a.name}"+(f" with {Path(exe).name}" if exe else "")+"...")
 if kind=="zip":
  with zipfile.ZipFile(a) as z:z.extractall(d)
  return
 if kind in("rar","unrar"):cmd=[exe,"x","-y","-o+","-idq",str(a),str(d)+os.sep]
 elif kind=="7z":cmd=[exe,"x","-y","-bd","-o"+str(d),str(a)]
 else:cmd=[exe,"-xf",str(a),"-C",str(d)]
 r=subprocess.run(cmd,capture_output=True,text=True,errors="replace",creationflags=NOWIN)
 if r.returncode!=0 and not walk(d):
  tail=(r.stderr or r.stdout or "").strip().splitlines()[-3:]
  raise RuntimeError(f"{Path(exe).name} failed on {a.name} (exit {r.returncode}): "+" / ".join(tail))
def extract_members(a,d,members,cfg):
 # only pulls the few files we actually need (modified .json) out of the previous
 # archive - unpacking the whole archive every run was pure waste.
 if not members:return False
 d.mkdir(parents=True,exist_ok=True)
 if a.suffix.lower()==".zip":
  try:
   with zipfile.ZipFile(a) as z:
    have=set(z.namelist())
    for m in members:
     if m in have:z.extract(m,d)
   return True
  except Exception:return False
 r=rar(cfg)
 if not r:
  # no WinRAR: fall back to unpacking everything with whatever can read the archive
  try:extract_all(a,d,cfg);return True
  except Exception:return False
 try:
  for i in range(0,len(members),50):
   ch=[m.replace("/",os.sep) for m in members[i:i+50]]
   subprocess.run([r,"x","-o+",str(a)]+ch+[str(d)+os.sep],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=NOWIN)
  return True
 except Exception:return False
def archive(src,dst,cfg,skipped=None):
 if cfg.get("archive_format","zip").lower()=="rar":
  r=rar(cfg)
  if not r:raise RuntimeError("RAR selected but WinRAR/Rar.exe was not found.")
  cmd=[r,"a","-r","-ep1",f"-mt{max(1,min(16,os.cpu_count() or 4))}",str(dst),str(Path(src)/"*")]
  pr=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=NOWIN)
  while pr.poll() is None:
   if CANCEL.wait(0.2):pr.kill();pr.wait();raise RuntimeError("cancelled")
  if pr.returncode>1:raise RuntimeError(f"WinRAR failed with exit code {pr.returncode}.")
 else:
  lv=cfg.get("compress_level")
  kw={} if lv is None else {"compresslevel":max(0,min(9,int(lv)))}
  base=Path(src)
  with zipfile.ZipFile(dst,"w",zipfile.ZIP_DEFLATED,**kw) as z:
   for p in walk(base):
    if CANCEL.is_set():break
    rel=p.relative_to(base).as_posix()
    try:z.write(p,rel)
    except OSError:
     if skipped is not None and rel not in skipped:skipped.append(rel)
  if CANCEL.is_set():raise RuntimeError("cancelled")
# -------------------------------------------------------- signatures ----
# Every backup is signed with an Ed25519 key pair that lives in the user's Windows profile
# (%APPDATA%\Backtrail\keys, made on first use). The signature covers the manifest's metadata,
# every file hash and the SHA-256 of the archive itself, so a changed archive, a changed
# manifest or a changed author claim all verify as INVALID. The public key is published into
# <backup root>\_keys\<user@host>.pub -- the trust store every verifier reads.
import base64
TRUST_DIR="_keys"
SIG_MARKS={"signed":"✔ signed","untrusted":"? untrusted key","invalid":"✖ INVALID","unsigned":"unsigned","unavailable":"? unverified"}
def _crypto():
 try:
  from cryptography.hazmat.primitives.asymmetric import ed25519
  from cryptography.hazmat.primitives import serialization
  return ed25519,serialization
 except Exception:return None
def signing_available():return _crypto() is not None
def signer_id():return "%s@%s"%identity()
def _safe(s):return "".join(c if c.isalnum() or c in "._-@" else "_" for c in s)
def keys_dir(cfg=None):
 d=Path((cfg or {}).get("keys_dir") or os.path.join(os.environ.get("APPDATA") or str(Path.home()),"Backtrail","keys"))
 d.mkdir(parents=True,exist_ok=True);return d
def key_fingerprint(raw):return hashlib.sha256(raw).hexdigest()[:16]
def _raw_pub(key):
 _,ser=_crypto();return key.public_key().public_bytes(ser.Encoding.Raw,ser.PublicFormat.Raw)
def pub_line(raw,signer=None):return f"ed25519 {base64.b64encode(raw).decode()} {signer or signer_id()}\n"
def read_pub(path):
 # the raw public key held by a .pub file (None when missing or unreadable)
 try:
  parts=Path(path).read_text(encoding="utf-8").split()
  if len(parts)>=2 and parts[0]=="ed25519":return base64.b64decode(parts[1])
 except (OSError,ValueError):pass
 return None
def load_or_create_key(cfg=None,log=None):
 # this user's private key, made on first use and never leaving the profile folder
 c=_crypto()
 if not c:return None
 ed,ser=c;d=keys_dir(cfg);priv=d/(_safe(signer_id())+".key")
 if priv.exists():return ser.load_pem_private_key(priv.read_bytes(),password=None)
 key=ed.Ed25519PrivateKey.generate()
 priv.write_bytes(key.private_bytes(ser.Encoding.PEM,ser.PrivateFormat.PKCS8,ser.NoEncryption()))
 (d/(_safe(signer_id())+".pub")).write_text(pub_line(_raw_pub(key)),encoding="utf-8")
 if log:log(f"Signing key created for {signer_id()}: {key_fingerprint(_raw_pub(key))} (kept in {d})")
 return key
def trust_dir(root):return Path(root)/TRUST_DIR
def publish_public_key(root,signer,raw,log=None):
 # the trust store beside the backups. A different key already registered for the same
 # signer is never overwritten: that is exactly the case a verifier must notice.
 d=trust_dir(root);d.mkdir(parents=True,exist_ok=True);p=d/(_safe(signer)+".pub");have=read_pub(p) if p.exists() else None
 if have is None:p.write_text(pub_line(raw,signer),encoding="utf-8");return "published"
 if have!=raw:
  if log:log(f"WARNING: {d} already holds another key for {signer}; backups signed on this machine verify as 'untrusted key' until {p.name} is replaced.")
  return "conflict"
 return "known"
def signed_payload(meta,files,archive_sha256):
 m={k:v for k,v in meta.items() if k!="signature"}
 return json.dumps({"meta":m,"files":files,"archive_sha256":archive_sha256},sort_keys=True,separators=(",",":")).encode("utf-8")
def sign_backup(meta,files,archive,cfg,log=None):
 # -> the signature block written into meta, or None when signing is not possible here
 key=load_or_create_key(cfg,log)
 if key is None or not Path(archive).is_file():return None
 ah=hash_file(archive);raw=_raw_pub(key)
 meta["signature"]={"alg":"ed25519","signer":signer_id(),"key_id":key_fingerprint(raw),"public_key":base64.b64encode(raw).decode(),
                    "archive_sha256":ah,"signed":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"sig":base64.b64encode(key.sign(signed_payload(meta,files,ah))).decode()}
 publish_public_key(cfg["backup_root"],signer_id(),raw,log)
 return meta["signature"]
def verify_manifest(manifest,trust=None):
 # the quick check, no archive read: does the signature verify, and is its key the one the trust
 # store holds for that signer? -> {status, detail, signer, key_id}
 # status: signed | untrusted | invalid | unsigned | unavailable
 manifest=Path(manifest)
 try:meta,files=load_manifest(manifest)
 except (OSError,ValueError) as ex:return {"status":"invalid","detail":f"manifest unreadable: {ex}","signer":"","key_id":""}
 sig=meta.get("signature")
 if not sig:return {"status":"unsigned","detail":"no signature (an old manifest, or signing switched off)","signer":"","key_id":""}
 out={"signer":sig.get("signer",""),"key_id":sig.get("key_id","")}
 c=_crypto()
 if not c:return dict(out,status="unavailable",detail="the cryptography package is not installed: cannot verify")
 ed,_=c
 try:
  raw=base64.b64decode(sig["public_key"]);pub=ed.Ed25519PublicKey.from_public_bytes(raw)
  pub.verify(base64.b64decode(sig["sig"]),signed_payload(meta,files,sig["archive_sha256"]))
 except Exception as ex:return dict(out,status="invalid",detail=f"the signature does not match the manifest ({ex.__class__.__name__})")
 if key_fingerprint(raw)!=sig.get("key_id"):return dict(out,status="invalid",detail="key id does not match the public key")
 t=Path(trust) if trust else trust_dir(manifest.parent.parent)
 known=read_pub(t/(_safe(sig.get("signer",""))+".pub")) if t.is_dir() else None
 if known is None:return dict(out,status="untrusted",detail=f"valid signature, but {sig.get('signer')} has no key in {t}")
 if known!=raw:return dict(out,status="untrusted",detail=f"valid signature, but not with the key {t} holds for {sig.get('signer')}")
 return dict(out,status="signed",detail=f"signed by {sig.get('signer')}, key {sig.get('key_id')}")
def verify_archive(backup_or_manifest,trust=None):
 # the deep check: the manifest as above, then the archive re-hashed against the signed value
 b=backup_or_manifest
 manifest=b.manifest if isinstance(b,Backup) else Path(b);archive=b.archive if isinstance(b,Backup) else None
 if manifest is None:return {"status":"unsigned","detail":"no manifest","signer":"","key_id":""}
 if archive is None:
  m=STAMP_RE.match(manifest.name)
  if m:
   for ext in (".zip",".rar"):
    p=manifest.with_name(f"{m['stamp']}_{m['name']}_PRODUCTION{ext}")
    if p.exists():archive=p;break
 r=verify_manifest(manifest,trust)
 if r["status"] in ("unsigned","unavailable","invalid"):return r
 if archive is None or not archive.exists():return dict(r,status="invalid",detail="the archive is missing")
 meta,_=load_manifest(manifest)
 if hash_file(archive)!=meta["signature"]["archive_sha256"]:return dict(r,status="invalid",detail="the archive's content differs from what was signed")
 return dict(r,detail=r["detail"]+"; archive intact")
# ------------------------------------------------------------- a run ----
def run(loc,cfg,log=print,note="",mode="manual"):
 # one backup of one source. `note` says why (free text from the GUI or --note),
 # `mode` is "manual" or "scheduled"; both go into the manifest and the changelog with the user name.
 src=Path(loc["path"])
 if not src.exists():raise FileNotFoundError(f"Source unavailable: {src}")
 name=loc.get("backup_name") or loc.get("name") or "Backup"
 safe=safe_name(name)
 od=Path(cfg["backup_root"])/safe;od.mkdir(parents=True,exist_ok=True)
 ext=".rar" if cfg.get("archive_format","zip").lower()=="rar" else ".zip"
 # the stamp has second resolution: two runs inside the same second would reuse the
 # same names and silently overwrite the previous backup, so wait for a free slot.
 for _ in range(40):
  stamp=datetime.now().strftime("%Y-%m-%d_%H-%M-%S");prefix=f"{stamp}_{safe}"
  ap=od/f"{prefix}_PRODUCTION{ext}";mp=od/f"{prefix}_MANIFEST.json";cp=od/f"{prefix}_CHANGELOG.txt"
  if not (ap.exists() or mp.exists() or cp.exists()):break
  time.sleep(0.25)
 # the archive (compression) and the scan (hashing) are independent reads of the
 # same tree, so run them concurrently instead of one after the other.
 arch_skip=[];arch_err=[];temp=od/".previous"
 def do_archive():
  try:archive(src,ap,cfg,arch_skip)
  except Exception as e:arch_err.append(str(e))
 log(f"Scanning {name} / creating {ap.name}...")
 at=threading.Thread(target=do_archive,daemon=True);at.start()
 skipped=[];new=scan(src,skipped,cfg)
 if CANCEL.is_set():at.join();_abort(ap,temp);raise RuntimeError("Backup cancelled by user")
 prev=[b for b in list_backups(od) if b.manifest and b.prefix!=prefix];last=prev[-1] if prev else None
 old=last.files if last else {}
 for rel in skipped:
  if rel in old:new[rel]=old[rel]
 mod=sorted(k for k in new.keys()&old.keys() if new[k]["sha256"]!=old[k]["sha256"])
 jmod=[r for r in mod if r.lower().endswith(".json")]
 shutil.rmtree(temp,ignore_errors=True);have=False
 if last and last.archive and jmod:
  try:have=extract_members(last.archive,temp,jmod,cfg)
  except:pass
 jd={}
 for rel in jmod:
  a=temp/Path(rel);b=src/Path(rel)
  if have and a.exists():
   j=jsondiff(a,b)
   if j:jd[rel]=j
 user,host=identity();now=datetime.now()
 meta={"version":MANIFEST_VERSION,"tool":f"{TOOL} v{VERSION}","date":now.strftime("%Y-%m-%d %H:%M:%S"),"name":name,"source":str(src),
       "user":user,"host":host,"mode":mode,"note":(note or "").strip(),"archive":ap.name,"previous":last.prefix if last else ""}
 head=[("Date",meta["date"]),("Name",name),("Source",str(src)),("By",f"{user} @ {host}"),("Mode",mode),("Note",meta["note"]),("Archive",ap.name),("Previous",last.prefix if last else "(none: first backup)")]
 at.join()
 if CANCEL.is_set():_abort(ap,temp);raise RuntimeError("Backup cancelled by user")
 if arch_err:log("WARNING: archive incomplete: "+arch_err[0])
 sig=None
 if cfg.get("sign",True) is not False:
  try:sig=sign_backup(meta,new,ap,cfg,log)
  except Exception as ex:log("WARNING: backup not signed: "+str(ex))
 head.append(("Signature",f"ed25519 key {sig['key_id']} by {sig['signer']}" if sig else "(none)"+("" if signing_available() else " -- the cryptography package is not installed")))
 allskip=sorted(set(skipped)|set(arch_skip))
 L,add,mod,delete=changelog_lines("PRODUCTION BACKUP CHANGELOG",head,old,new,jd,skipped=allskip,arch_err=arch_err[0] if arch_err else None)
 if allskip:log(f"WARNING: {len(allskip)} file(s) skipped (in use / unreadable).")
 save_manifest(mp,new,meta);cp.write_text("\n".join(L),encoding="utf-8");shutil.rmtree(temp,ignore_errors=True)
 log(f"SUCCESS: +{len(add)} ~{len(mod)} -{len(delete)}"+(f" !{len(allskip)}" if allskip else ""))
 try:
  kl=int(cfg.get("keep_last",0) or 0);kd=int(cfg.get("keep_days",0) or 0)
  if kl>0 or kd>0:prune(od,kl,kd,log)
 except Exception as e:log("WARNING: retention could not be applied: "+str(e))
 return cp
# -------------------------------------------------------- comparing ----
def _files_of(x,cfg,temp,log=None):
 # (meta, files, archive, label) for a Backup or a bare archive path. The manifest is used
 # when it exists; otherwise the archive is unpacked and every file hashed.
 if isinstance(x,Backup):
  if x.manifest:return x.meta,x.files,x.archive,x.label()
  if not x.archive:raise RuntimeError(f"{x.prefix}: neither a manifest nor an archive.")
  path=x.archive
 else:path=Path(x)
 if not path.is_file():raise FileNotFoundError(f"Archive not found: {path}")
 m=STAMP_RE.match(path.name)
 if m:
  mp=path.with_name(f"{m['stamp']}_{m['name']}_MANIFEST.json")
  if mp.exists():
   meta,files=load_manifest(mp)
   if not meta.get("user"):
    cl=path.with_name(f"{m['stamp']}_{m['name']}_CHANGELOG.txt")
    if cl.exists():meta={**header_meta(cl),**meta}
   return meta,files,path,f"{path.name}  by {meta.get('user') or '(unknown)'}  {SIG_MARKS.get(verify_manifest(mp)['status'],'')}"
 d=Path(temp);extract_all(path,d,cfg,log)
 # a zip made of the folder itself holds one top folder: descend into it
 top=[p for p in d.iterdir()]
 if len(top)==1 and top[0].is_dir():d=top[0]
 files=scan(d,None,cfg);t=parse_stamp(path.name)
 meta={"source":str(path),"date":(t or datetime.fromtimestamp(path.stat().st_mtime)).strftime("%Y-%m-%d %H:%M:%S"),"hashed_from_archive":True}
 return meta,files,path,f"{path.name}  (no manifest: files hashed from the archive)"
def _stamp_of(x,meta):
 if isinstance(x,Backup):return x.stamp
 t=parse_stamp(Path(x).name)
 if t:return t.strftime("%Y-%m-%d_%H-%M-%S")
 return (meta.get("date") or "").replace(" ","_").replace(":","-") or Path(x).stem
def _name_of(x):
 # the source name: a Backup's own, or an archive's file name without its timestamp
 if isinstance(x,Backup):return x.name
 m=STAMP_RE.match(Path(x).name)
 if m:return m["name"]
 stem=Path(x).stem
 stem=re.sub(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(?:-\d{2})?|(?<!\d)\d{8}_\d{4}(?:\d{2})?(?!\d)","",stem)
 return re.sub(r"[_\-\s]+$|^[_\-\s]+","",stem).replace("__","_") or Path(x).stem
def compare(old,new,cfg,log=print,out=None):
 # the changelog between ANY two backups -- Backup objects of one source, or two archive
 # files (.rar / .zip) from anywhere. Returns (path written, (added, modified, deleted)).
 temp=Path(tempfile.mkdtemp(prefix="backtrail_cmp_"))
 try:
  om,of,oa,ol=_files_of(old,cfg,temp/"old",log);nm,nf,na,nl=_files_of(new,cfg,temp/"new",log)
  os_,ns_=_stamp_of(old,om),_stamp_of(new,nm)
  if os_>ns_:
   log("Note: the two backups were given newest first; comparing from the older to the newer.")
   om,of,oa,ol,nm,nf,na,nl,os_,ns_,old,new=nm,nf,na,nl,om,of,oa,ol,ns_,os_,new,old
  jmod=sorted(r for r in nf.keys()&of.keys() if nf[r]["sha256"]!=of[r]["sha256"] and r.lower().endswith(".json"))
  jd={}
  if jmod:
   for side,arch,m in (("old",oa,om),("new",na,nm)):
    if not m.get("hashed_from_archive") and arch:   # already unpacked otherwise
     try:extract_members(arch,temp/side,jmod,cfg)
     except Exception:pass
   for r in jmod:
    a=_find(temp/"old",r);b=_find(temp/"new",r)
    if a and b:
     x=jsondiff(a,b)
     if x:jd[r]=x
  between=[]
  if isinstance(old,Backup) and isinstance(new,Backup) and old.folder==new.folder:
   for b in list_backups(old.folder):
    if os_<b.stamp<=ns_:
     c=summary_counts(b.changelog) if b.changelog else {"added":"?","modified":"?","deleted":"?"}
     m=b.meta
     between.append(f"{b.stamp}  by {b.by()}"+(f"  [{m['mode']}]" if m.get("mode") else "")+f"  +{c['added']} ~{c['modified']} -{c['deleted']}"+(f"  - {m['note']}" if m.get("note") else ""))
   if not between:between=["(none: consecutive backups)"]
  name=_name_of(old)
  head=[("Date",datetime.now().strftime("%Y-%m-%d %H:%M:%S")),("Name",name),("From",ol),("From taken",om.get("date","")),("From note",om.get("note","")),
        ("To",nl),("To taken",nm.get("date","")),("To note",nm.get("note","")),("Compared by",who())]
  L,add,mod,delete=changelog_lines("BACKUP COMPARISON",head,of,nf,jd,between=between)
  if out is None:
   # inside a source folder the folder already names the source -- keep the file name short
   # (a long backup root plus two stamps plus the name can pass Windows' 260-character limit)
   if isinstance(old,Backup):out=old.folder/f"{os_}_to_{ns_}_COMPARE.txt"
   else:
    folder=Path(new if not isinstance(new,Backup) else new.archive).parent
    out=folder/f"{os_}_to_{ns_}_{''.join(c if c.isalnum() or c in '._-' else '_' for c in name)}_COMPARE.txt"
  out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text("\n".join(L),encoding="utf-8")
  log(f"COMPARED {os_} -> {ns_}: +{len(add)} ~{len(mod)} -{len(delete)}  ({out.name})")
  return out,(len(add),len(mod),len(delete))
 finally:shutil.rmtree(temp,ignore_errors=True)
def _find(root,rel):
 # the extracted copy of `rel` under root (directly, or under the single top folder of a zip)
 p=Path(root)/Path(rel)
 if p.exists():return p
 try:top=[x for x in Path(root).iterdir()]
 except OSError:return None
 if len(top)==1 and top[0].is_dir() and (top[0]/Path(rel)).exists():return top[0]/Path(rel)
 return None
# ---------------------------------------------------------- retention ----
def prune(folder,keep_last=0,keep_days=0,log=print):
 # remove backups beyond the newest keep_last and/or older than keep_days (0 = no limit).
 # The newest complete backup is never removed: the next run needs it to detect changes.
 # Only the three files of a backup are touched; comparisons (*_COMPARE.txt) stay.
 bks=list_backups(folder)
 if not bks or (keep_last<=0 and keep_days<=0):return []
 newest=next((b for b in reversed(bks) if b.complete),None)
 drop=set()
 if keep_last>0 and len(bks)>keep_last:drop|={b.prefix for b in bks[:-keep_last]}
 if keep_days>0:
  cut=time.time()-keep_days*86400.0
  drop|={b.prefix for b in bks if b.time.timestamp()<cut}
 removed=[]
 for b in bks:
  if b.prefix not in drop or (newest and b.prefix==newest.prefix):continue
  by,when=b.by(),b.time          # read before the manifest is gone
  for p in b.paths():
   try:p.unlink()
   except OSError as e:log(f"WARNING: could not remove {p.name}: {e}");continue
  removed.append(b.prefix);log(f"Retention: removed backup {b.prefix} (by {by}, {when:%Y-%m-%d %H:%M})")
 if removed:log(f"Retention: {len(removed)} old backup(s) removed, {len(bks)-len(removed)} kept.")
 return removed
# ----------------------------------------------------------- restore ----
# Putting a backup back: into a fresh folder, or over the live PRODUCTION folder it came from.
# Every file is checked against the signed manifest hash BEFORE anything is written, so a
# tampered archive restores nothing. A restore over the source is always preceded by a safety
# backup of the source (what you are about to overwrite is itself kept and signed). Each
# restore leaves a report, <when>_from_<backup stamp>_RESTORE.txt, next to the backups.
RESTORE_RE=re.compile(r"^(?P<when>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_from_(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_RESTORE\.txt$",re.I)
def location_for(backup,cfg=None):
 # the configured source this backup belongs to (same backup name), or None
 for l in (cfg or {}).get("locations") or []:
  if safe_name(l.get("backup_name") or l.get("name") or "")==backup.folder.name:return l
 return None
def source_of(backup,cfg=None):
 # the live folder a backup was taken from: the configured location first (the plant may have
 # moved), then the Source recorded in the manifest / changelog; None when neither is known
 l=location_for(backup,cfg)
 if l and l.get("path"):return Path(l["path"])
 s=backup.meta.get("source")
 return Path(s) if s else None
def _archive_files(backup,temp,cfg,log=None):
 # (base folder, [rel...]) of a fully unpacked archive -- for backups that have no manifest
 extract_all(backup.archive,temp,cfg,log)
 base=Path(temp);top=list(base.iterdir())
 if len(top)==1 and top[0].is_dir():base=top[0]
 return base,sorted(p.relative_to(base).as_posix() for p in walk(base))
def restore_plan(backup,target,members=None,cfg=None):
 # what restoring `backup` into `target` would do, touching nothing:
 #   add      - files the target does not have
 #   replace  - files that exist in the target with different content
 #   same     - files already identical (skipped by the restore)
 #   extra    - files in the target the backup does not know (only removed by clean=True)
 #   missing  - names asked for that the backup does not contain
 files=backup.files;target=Path(target)
 if members is None:want=sorted(files)
 else:want=[m for m in members if m in files]
 plan={"add":[],"replace":[],"same":[],"extra":[],"missing":[] if members is None else [m for m in members if m not in files]}
 live={}
 if target.is_dir():
  if members is None:live=scan(target,None,cfg)
  else:
   for rel in want:
    p=target/Path(rel)
    try:
     if p.is_file():live[rel]={"size":p.stat().st_size,"sha256":hash_file(p)}
    except OSError:live[rel]={"size":-1,"sha256":""}
 for rel in want:
  if rel not in live:plan["add"].append(rel)
  elif live[rel]["sha256"]==files[rel]["sha256"]:plan["same"].append(rel)
  else:plan["replace"].append(rel)
 if members is None:plan["extra"]=sorted(set(live)-set(files))
 return plan
def restore(backup,target,cfg,members=None,log=print,clean=False,safety=None,record=True,signature=None):
 # copy the files of `backup` (all, or only `members`) into `target`, after checking every one
 # against the manifest hash: a single mismatch stops the restore before a file is written.
 # clean=True (full restore only) also removes files of `target` the backup does not have.
 # Returns {"restored","skipped","removed","failed":[(rel,why)],"unverified","record"}.
 if not backup.archive:raise FileNotFoundError(f"{backup.prefix}: the archive is missing, nothing can be restored.")
 if not backup.archive.is_file():raise FileNotFoundError(f"Archive not found: {backup.archive}")
 files=backup.files;target=Path(target)
 if members is not None:
  members=list(dict.fromkeys(members))
  if not members:raise ValueError("No files selected.")
  if files:
   unknown=[m for m in members if m not in files]
   if unknown:raise ValueError("Not in this backup: "+", ".join(unknown[:5])+(" ..." if len(unknown)>5 else ""))
 temp=Path(tempfile.mkdtemp(prefix="backtrail_rst_"))
 try:
  log(f"Restoring {backup.prefix}"+(f" ({len(members)} file(s))" if members is not None else "")+f" -> {target}")
  if files and members is not None:
   if not extract_members(backup.archive,temp,members,cfg):raise RuntimeError(f"Could not extract from {backup.archive.name}.")
   want=members;base=temp
  elif files:
   extract_all(backup.archive,temp,cfg,log);want=sorted(files);base=temp
  else:
   base,want=_archive_files(backup,temp,cfg,log)   # no manifest: whatever the archive holds, unverified
   if members is not None:
    unknown=[m for m in members if m not in want]
    if unknown:raise ValueError("Not in this archive: "+", ".join(unknown[:5]))
    want=members
  # pass 1: find and verify every file; nothing is written until all of them check out
  src={};bad=[];out={"restored":[],"skipped":[],"removed":[],"failed":[],"unverified":[],"record":None}
  for rel in want:
   if CANCEL.is_set():raise RuntimeError("Restore cancelled by user")
   p=_find(base,rel)
   if not p:bad.append((rel,"not in the archive"));continue
   exp=files.get(rel,{}).get("sha256")
   if exp:
    if hash_file(p)!=exp:bad.append((rel,"content differs from the signed manifest"));continue
   else:out["unverified"].append(rel)
   src[rel]=p
  if bad:
   raise RuntimeError(f"Restore refused: {len(bad)} file(s) of {backup.archive.name} do not match its manifest -- "+"; ".join(f"{r} ({w})" for r,w in bad[:3])+(" ..." if len(bad)>3 else ""))
  # pass 2: copy (unchanged files are left alone, so their timestamps stay)
  target.mkdir(parents=True,exist_ok=True)
  for rel in want:
   if CANCEL.is_set():raise RuntimeError("Restore cancelled by user")
   dst=target/Path(rel);exp=files.get(rel,{}).get("sha256")
   try:
    if exp and dst.is_file() and dst.stat().st_size==files[rel]["size"] and hash_file(dst)==exp:out["skipped"].append(rel);continue
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src[rel],dst);out["restored"].append(rel)
   except OSError as e:out["failed"].append((rel,str(e)))
  if clean and members is None:
   keep=set(want)
   for p in walk(target):
    rel=p.relative_to(target).as_posix()
    if rel in keep:continue
    try:p.unlink();out["removed"].append(rel)
    except OSError as e:out["failed"].append((rel,"could not remove: "+str(e)))
   for d in sorted((x for x in target.rglob("*") if x.is_dir()),key=lambda x:len(x.parts),reverse=True):
    try:d.rmdir()
    except OSError:pass
  if record:
   try:out["record"]=write_restore_record(backup,target,out,members is not None,clean,safety,signature)
   except OSError as e:log("WARNING: restore report not written: "+str(e))
  log(("SUCCESS: " if not out["failed"] else "WARNING: ")+f"restored {len(out['restored'])} file(s)"+(f", {len(out['skipped'])} already identical" if out["skipped"] else "")
      +(f", {len(out['removed'])} removed" if out["removed"] else "")+(f", {len(out['failed'])} FAILED" if out["failed"] else "")+(f", {len(out['unverified'])} unverified (no manifest)" if out["unverified"] else ""))
  return out
 finally:shutil.rmtree(temp,ignore_errors=True)
def write_restore_record(backup,target,out,partial,clean,safety=None,signature=None):
 now=datetime.now();sig=signature or (backup.signature() if backup.manifest else {"status":"unsigned","detail":"no manifest"})
 head=[("Date",now.strftime("%Y-%m-%d %H:%M:%S")),("Name",backup.name),("Restored from",backup.prefix),("Backup taken",backup.meta.get("date","")),("Backup by",backup.by()),
       ("Backup note",backup.meta.get("note","")),("Backup signature",f"{SIG_MARKS.get(sig['status'],sig['status'])} - {sig['detail']}"),("Restored to",str(target)),
       ("Restored by",who()),("Mode","selected files" if partial else ("full restore, extra files removed" if clean else "full restore")),("Safety backup",safety or "(none: restored into a separate folder)")]
 L=[TOOL,TAGLINE,"="*76,"RESTORE REPORT"]+[f"{k}: {v}" for k,v in head if v not in (None,"")]+["="*76,""]
 L+=["RESTORED","-"*76]+([f"> {x}" for x in out["restored"]] or ["(none)"])+["","ALREADY IDENTICAL (left untouched)","-"*76]+([f"= {x}" for x in out["skipped"]] or ["(none)"])
 L+=["","REMOVED (not in the backup)","-"*76]+([f"- {x}" for x in out["removed"]] or ["(none)"])
 if out["failed"]:L+=["","FAILED","-"*76]+[f"! {r}: {w}" for r,w in out["failed"]]
 if out["unverified"]:L+=["","NOT VERIFIED (the backup has no manifest)","-"*76]+[f"? {x}" for x in out["unverified"]]
 L+=["","SUMMARY","-"*76,f"Restored: {len(out['restored'])}",f"Identical: {len(out['skipped'])}",f"Removed: {len(out['removed'])}",f"Failed: {len(out['failed'])}"]
 p=backup.folder/f"{now:%Y-%m-%d_%H-%M-%S}_from_{backup.stamp}_RESTORE.txt"
 for i in range(1,100):
  if not p.exists():break
  p=p.with_name(f"{now:%Y-%m-%d_%H-%M-%S}-{i}_from_{backup.stamp}_RESTORE.txt")
 p.write_text("\n".join(L),encoding="utf-8");return p
def restore_to_source(backup,cfg,members=None,log=print,clean=False,note=""):
 # restore over the live folder the backup came from. A safety backup of that folder is taken
 # first (signed, with a note), so the state being overwritten is never lost; retention is not
 # applied during that run, because it could remove the very backup about to be restored.
 src=source_of(backup,cfg)
 if not src:raise RuntimeError(f"{backup.prefix}: the source folder of this backup is not known (not in the manifest, no configured source named {backup.folder.name}).")
 if not src.is_dir():raise FileNotFoundError(f"Source unavailable: {src}")
 loc=dict(location_for(backup,cfg) or {"name":backup.name,"path":str(src)},backup_name=backup.folder.name)   # the safety backup lands beside the backup being restored
 scfg=dict(cfg,keep_last=0,keep_days=0)
 log(f"Safety backup of {src} before the restore...")
 cp=run(loc,scfg,log,note=(note or "").strip() or f"safety backup before restore of {backup.stamp}",mode="manual")
 safety=cp.name[:-len("_CHANGELOG.txt")]
 out=restore(backup,src,cfg,members,log,clean=clean,safety=safety)
 out["safety"]=safety;return out
def list_restores(folder):
 # the restore reports of a source folder, newest first
 folder=Path(folder)
 if not folder.is_dir():return []
 return sorted((p for p in folder.iterdir() if RESTORE_RE.match(p.name)),key=lambda p:p.name,reverse=True)
