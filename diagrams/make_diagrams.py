"""Generates the UML class diagrams straight from the source code.

    python3 make_diagrams.py          -> class-diagram-overview.html and class-diagrams.html

Members are read from the real code with `ast`, so the diagrams always match it.
Open the HTML files in a browser and print them (landscape) or save them as PDF.
"""
from __future__ import annotations

import ast
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "laptop"
OUT = Path(__file__).resolve().parent

# --------------------------------------------------------------- read code
def read_classes() -> dict[str, dict]:
    classes = {}
    for path in sorted(list(ROOT.glob("*.py")) + list(ROOT.glob("ndn/*.py")) + list(ROOT.glob("forum/*.py"))):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes[node.name] = describe(node, path.relative_to(ROOT).as_posix())
    return classes


def ann(a) -> str:
    return ast.unparse(a).replace("'", "").replace('"', "") if a is not None else ""


def describe(node: ast.ClassDef, file: str) -> dict:
    bases = [ast.unparse(b) for b in node.bases]
    methods, attrs, abstract_count = [], [], 0
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name.startswith("__") and item.name != "__init__" and item.name != "__call__":
                continue
            decos = [ast.unparse(d) for d in item.decorator_list]
            is_abs = "abstractmethod" in decos
            abstract_count += is_abs
            args = [a for a in item.args.args if a.arg not in ("self", "cls")]
            params = ", ".join(a.arg + (": " + ann(a.annotation) if a.annotation is not None else "") for a in args)
            if item.args.vararg:
                params += (", " if params else "") + "*" + item.args.vararg.arg
            ret = ann(item.returns)
            vis = "-" if item.name.startswith("_") and item.name not in ("__init__", "__call__") else "+"
            static = "staticmethod" in decos or "classmethod" in decos
            prop = "property" in decos
            if prop:
                attrs.append({"vis": "+", "text": "%s: %s" % (item.name, ret or "?"), "static": False})
                continue
            methods.append({"vis": vis, "text": "%s(%s)%s" % (item.name, params, ": " + ret if ret else ""),
                            "abstract": is_abs, "static": static, "name": item.name})
            if item.name == "__init__":
                for st in ast.walk(item):
                    targets = []
                    if isinstance(st, ast.Assign):
                        targets = st.targets
                    elif isinstance(st, ast.AnnAssign):
                        targets = [st.target]
                    for t in targets:
                        for tt in (t.elts if isinstance(t, ast.Tuple) else [t]):
                            if isinstance(tt, ast.Attribute) and isinstance(tt.value, ast.Name) and tt.value.id == "self":
                                name = tt.attr
                                if not any(a["text"].split(":")[0] == name for a in attrs):
                                    attrs.append({"vis": "-" if name.startswith("_") else "+", "text": name, "static": False})
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            attrs.append({"vis": "+", "text": "%s: %s" % (item.target.id, ann(item.annotation)), "static": False})
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                for tt in (t.elts if isinstance(t, ast.Tuple) else [t]):
                    if isinstance(tt, ast.Name) and tt.id.isupper():
                        attrs.append({"vis": "+", "text": tt.id, "static": True})
                    elif isinstance(tt, ast.Name) and not tt.id.startswith("_"):
                        attrs.append({"vis": "+", "text": tt.id, "static": True})
    # constructor shown last-first like UML convention: keep order
    kind = "class"
    if "ABC" in bases:
        real = [m for m in methods if m["name"] != "__init__"]
        kind = "interface" if real and all(m["abstract"] for m in real) and not attrs else "abstract"
    elif any(isinstance(d, ast.Name) and d.id == "dataclass" or (isinstance(d, ast.Call) and ast.unparse(d.func) == "dataclass") for d in node.decorator_list):
        kind = "dataclass"
    return {"name": node.name, "bases": [b for b in bases if b != "ABC"], "methods": methods, "attrs": attrs,
            "kind": kind, "file": file, "doc": (ast.get_docstring(node) or "").split("\n")[0]}


# --------------------------------------------------------------- pattern labels
PATTERN = {
    "LoRaLinkFacade": "Facade", "NdnNode": "Facade", "LoRaForumApp": "Facade",
    "PacketFactory": "Factory Method", "ForumEventFactory": "Factory Method", "TransportFactory": "Factory Method",
    "UserInteractionFactory": "Factory Method",
    "RemoteForumProxy": "Proxy", "LocalForumEventSource": "Proxy: real subject", "ForumEventSource": "Proxy: subject",
    "PacketHandler": "Chain of Responsibility", "DuplicateInterestFilter": "CoR handler",
    "ContentStoreResponder": "CoR handler", "ProducerResponder": "CoR handler",
    "PendingInterestSatisfier": "CoR handler", "UnhandledPacketLogger": "CoR handler",
    "ForumEvent": "Command", "TopicCreated": "Command", "CommentAdded": "Command", "VoteCast": "Command",
    "UserInteraction": "Command + Template Method", "RegisterInteraction": "Command", "LoginInteraction": "Command",
    "CreateTopicInteraction": "Command", "AddCommentInteraction": "Command", "CastVoteInteraction": "Command",
    "SyncNowInteraction": "Command",
    "ForumEventBus": "Observer: subject", "ActivityFeed": "Observer", "RadioTrafficLog": "Observer",
    "TopicRankingStrategy": "Strategy", "NewestFirst": "Strategy", "Trending": "Strategy", "TopVoted": "Strategy",
    "MostDiscussed": "Strategy", "Producer": "Strategy", "FunctionProducer": "Strategy", "ForumLogProducer": "Strategy",
    "SerialTransport": "Adapter: target", "PyserialTransport": "Adapter", "PosixTtyTransport": "Adapter",
    "MemoryTransport": "Adapter",
    "TopicRepository": "Repository", "CommentRepository": "Repository", "VoteRepository": "Repository",
    "UserRepository": "Repository", "EventLogRepository": "Repository", "PeerProgressRepository": "Repository",
    "ForumRepository": "Repository",
}

# --------------------------------------------------------------- pages
# rows: list of rows, each row a list of class names. edges: (from, to, kind, label)
#   kinds: inherit (hollow triangle), uses (dashed arrow), has (solid arrow), creates (dashed, «create»)
FULL_PAGES = [
    {"title": "1. NDN packets: Factory Method", "note":
        "PacketFactory is the only place that turns raw LoRa bytes into Interest / Data objects (Factory Method). "
        "Segmenter splits big content into Data packets of at most 255 bytes.",
     "rows": [["PacketFactory", "Segmenter", "PacketError"], ["Packet"], ["Interest", "Data"]],
     "edges": [("PacketFactory", "Interest", "creates", ""), ("PacketFactory", "Data", "creates", ""),
               ("Segmenter", "PacketFactory", "uses", "")]},
    {"title": "2. LoRa link and serial adapters: Facade, Adapter, Factory Method", "note":
        "LoRaLinkFacade hides USB, hex encoding and the Pico protocol behind send() / on_frame(). "
        "Three adapters make different serial APIs look like one SerialTransport; TransportFactory picks one.",
     "rows": [["LoRaLinkFacade", "TransportFactory", "LinkError"], ["SerialTransport"],
              ["PyserialTransport", "PosixTtyTransport", "MemoryTransport"]],
     "edges": [("LoRaLinkFacade", "SerialTransport", "has", "transport"),
               ("TransportFactory", "SerialTransport", "creates", "")]},
    {"title": "3. NDN node: Facade, Observer, Strategy", "note":
        "NdnNode is the facade of the whole NDN layer: fetch(), publish(), register_producer(). "
        "It owns the Content Store, Pending Interest Table and nonce log, and runs every incoming packet through the handler chain.",
     "rows": [["NdnNode", "Producer"], ["ContentStore", "PendingInterestTable", "NonceLog", "PacketHandler", "LoRaLinkFacade", "FunctionProducer"],
              ["PendingEntry"]],
     "brief": ["PacketHandler", "LoRaLinkFacade"],
     "edges": [("NdnNode", "ContentStore", "has", ""), ("NdnNode", "PendingInterestTable", "has", ""),
               ("NdnNode", "NonceLog", "has", ""), ("NdnNode", "Producer", "has", "0..*"),
               ("NdnNode", "PacketHandler", "has", "chain"), ("NdnNode", "LoRaLinkFacade", "has", "link"),
               ("PendingInterestTable", "PendingEntry", "has", "0..*")]},
    {"title": "4. Incoming packets: Chain of Responsibility + Template Method", "note":
        "handle() is the template method: try this handler, otherwise pass to the next. Order: "
        "DuplicateInterestFilter → ContentStoreResponder → ProducerResponder → PendingInterestSatisfier → UnhandledPacketLogger.",
     "rows": [["PacketHandler"], ["DuplicateInterestFilter", "ContentStoreResponder", "ProducerResponder"],
              ["PendingInterestSatisfier", "UnhandledPacketLogger"]],
     "edges": [("PacketHandler", "PacketHandler", "has", "next")]},
    {"title": "5. Forum rules: Command, Factory Method, Observer", "note":
        "Every change is a ForumEvent command with apply(). The same object is saved, sent over LoRa and replayed on the other laptop. "
        "ForumEventBus notifies observers (ActivityFeed) without ForumService knowing them.",
     "rows": [["ForumService", "ForumEventFactory", "ForumEventBus"], ["ForumEvent", "ActivityFeed", "RadioTrafficLog"],
              ["TopicCreated", "CommentAdded", "VoteCast"], ["Topic", "Comment", "Vote", "User", "TopicPage"]],
     "edges": [("ForumService", "ForumEvent", "creates", ""), ("ForumEventFactory", "ForumEvent", "creates", ""),
               ("ForumService", "ForumEventBus", "has", "bus"), ("ForumEventBus", "ActivityFeed", "has", "listeners"),
               ("TopicCreated", "Topic", "has", ""), ("CommentAdded", "Comment", "has", ""), ("VoteCast", "Vote", "has", "")]},
    {"title": "6. Sorting topics: Strategy", "note":
        "ForumService asks RankingRegistry for the strategy the user picked and calls rank(). New ordering = new class.",
     "rows": [["ForumService", "RankingRegistry"], ["TopicRankingStrategy"], ["NewestFirst", "Trending", "TopVoted", "MostDiscussed"]],
     "edges": [("ForumService", "RankingRegistry", "has", "rankings"), ("RankingRegistry", "TopicRankingStrategy", "has", "1..*")]},
    {"title": "7. Storage: Repository + Interface Segregation + Dependency Inversion", "note":
        "ForumService only knows the small repository interfaces. The SQLite classes implement them; create_sqlite_repository() wires them.",
     "rows": [["ForumRepository", "SqliteDatabase"],
              ["UserRepository", "TopicRepository", "CommentRepository", "VoteRepository", "EventLogRepository", "PeerProgressRepository"],
              ["SqliteUserRepository", "SqliteTopicRepository", "SqliteCommentRepository", "SqliteVoteRepository",
               "SqliteEventLogRepository", "SqlitePeerProgressRepository"]],
     "edges": [("ForumRepository", "UserRepository", "has", ""), ("ForumRepository", "TopicRepository", "has", ""),
               ("ForumRepository", "CommentRepository", "has", ""), ("ForumRepository", "VoteRepository", "has", ""),
               ("ForumRepository", "EventLogRepository", "has", ""), ("ForumRepository", "PeerProgressRepository", "has", "")]},
    {"title": "8. Syncing with the other laptop: Proxy", "note":
        "RemoteForumProxy has the same interface as LocalForumEventSource, but the forum it stands for is on the OTHER laptop. "
        "It turns latest_sequence() / get_event() into NDN Interests over LoRa, retries, caches and tracks if the peer is online.",
     "rows": [["PeerSynchronizer", "ForumEventSource", "ForumLogPublisher"],
              ["ForumService", "RemoteForumProxy", "LocalForumEventSource", "ForumLogProducer", "NdnNode"]],
     "brief": ["NdnNode", "ForumService"],
     "edges": [("PeerSynchronizer", "RemoteForumProxy", "has", "1..*"), ("PeerSynchronizer", "ForumService", "uses", "apply_remote"),
               ("RemoteForumProxy", "NdnNode", "uses", "fetch"), ("ForumLogPublisher", "ForumLogProducer", "creates", ""),
               ("ForumLogProducer", "LocalForumEventSource", "has", ""), ("ForumLogPublisher", "NdnNode", "uses", "register_producer")]},
    {"title": "9. User actions and web: UserInteractionFactory (Factory Method + Command)", "note":
        "POST /api/<action> → UserInteractionFactory.create(action) → interaction.run(ctx). "
        "run() is a template method: check login → check fields → execute().",
     "rows": [["ForumWebApi", "UserInteractionFactory", "InteractionContext"], ["UserInteraction", "AuthService"],
              ["RegisterInteraction", "LoginInteraction", "CreateTopicInteraction"],
              ["AddCommentInteraction", "CastVoteInteraction", "SyncNowInteraction"], ["PasswordHasher", "TokenSigner"]],
     "edges": [("ForumWebApi", "UserInteractionFactory", "uses", ""), ("UserInteractionFactory", "UserInteraction", "creates", ""),
               ("UserInteraction", "InteractionContext", "uses", "run(ctx)"), ("AuthService", "PasswordHasher", "has", ""),
               ("AuthService", "TokenSigner", "has", "")]},
    {"title": "10. Composition root: LoRaForumApp (Facade + Dependency Injection)", "note":
        "app.py is the only place that creates concrete classes and plugs them together. Everything else receives its dependencies.",
     "rows": [["LoRaForumApp"], ["ForumWebApi", "ForumService", "AuthService", "PeerSynchronizer"],
              ["NdnNode", "ForumRepository", "UserInteractionFactory", "RemoteForumProxy"]],
     "edges": [("LoRaForumApp", "ForumWebApi", "has", "api"), ("LoRaForumApp", "ForumService", "has", "forum"),
               ("LoRaForumApp", "AuthService", "has", "auth"), ("LoRaForumApp", "PeerSynchronizer", "has", "sync"),
               ("LoRaForumApp", "NdnNode", "has", "node"), ("LoRaForumApp", "ForumRepository", "has", "repo"),
               ("LoRaForumApp", "UserInteractionFactory", "has", "interactions"), ("LoRaForumApp", "RemoteForumProxy", "has", "proxies")],
     "compact": True},
]

# overview: the main classes with only their key methods
OVERVIEW_METHODS = {
    "LoRaForumApp": ["start", "sync_now"],
    "ForumWebApi": ["handle_get", "handle_post"],
    "UserInteractionFactory": ["create", "register"],
    "UserInteraction": ["run", "execute"],
    "ForumService": ["create_topic", "add_comment", "vote", "apply_remote", "list_topics"],
    "ForumEvent": ["apply", "to_wire"],
    "ForumEventBus": ["subscribe", "publish"],
    "TopicRankingStrategy": ["rank"],
    "ForumRepository": [],
    "PeerSynchronizer": ["sync_once"],
    "ForumEventSource": ["latest_sequence", "get_event"],
    "RemoteForumProxy": ["latest_sequence", "get_event"],
    "LocalForumEventSource": ["latest_sequence", "get_event"],
    "NdnNode": ["fetch", "publish", "register_producer"],
    "PacketHandler": ["handle", "set_next"],
    "PacketFactory": ["interest", "data", "decode"],
    "LoRaLinkFacade": ["send", "on_frame"],
    "SerialTransport": ["write_line", "read_line"],
}
OVERVIEW_PAGES = [
    {"title": "Class diagram overview: forum side", "note":
        "Start here: the main classes, how they call each other, and the pattern each one implements. Pages 1 to 10 of the full diagrams show every attribute and method.",
     "rows": [["LoRaForumApp", "ForumWebApi", "UserInteractionFactory", "UserInteraction"],
              ["ForumService", "ForumEvent", "ForumEventBus", "TopicRankingStrategy"],
              ["ForumRepository", "PeerSynchronizer", "ForumEventSource"],
              ["LocalForumEventSource", "RemoteForumProxy"]],
     "edges": [("LoRaForumApp", "ForumWebApi", "has", ""), ("ForumWebApi", "UserInteractionFactory", "uses", ""),
               ("UserInteractionFactory", "UserInteraction", "creates", ""), ("UserInteraction", "ForumService", "uses", ""),
               ("ForumService", "ForumEvent", "creates", ""), ("ForumService", "ForumEventBus", "has", ""),
               ("ForumService", "TopicRankingStrategy", "uses", ""), ("ForumService", "ForumRepository", "has", ""),
               ("PeerSynchronizer", "ForumEventSource", "uses", ""), ("PeerSynchronizer", "ForumService", "uses", ""),
               ("RemoteForumProxy", "ForumEventSource", "inherit", ""), ("LocalForumEventSource", "ForumEventSource", "inherit", "")],
     "simple": True},
    {"title": "Class diagram overview: network side", "note":
        "RemoteForumProxy (previous page) calls NdnNode.fetch(). Everything below it is the NDN-over-LoRa stack.",
     "rows": [["RemoteForumProxy", "NdnNode", "PacketFactory"], ["PacketHandler", "LoRaLinkFacade"], ["SerialTransport"]],
     "edges": [("RemoteForumProxy", "NdnNode", "uses", "fetch"), ("NdnNode", "PacketFactory", "has", ""),
               ("NdnNode", "PacketHandler", "has", "chain"), ("NdnNode", "LoRaLinkFacade", "has", ""),
               ("LoRaLinkFacade", "SerialTransport", "has", ""), ("PacketHandler", "PacketHandler", "has", "next")],
     "simple": True},
]


# --------------------------------------------------------------- render
def box_html(c: dict, simple: bool, compact: bool) -> str:
    stereo = []
    if c["kind"] in ("interface", "abstract"):
        stereo.append("«%s»" % c["kind"])
    if c["kind"] == "dataclass":
        stereo.append("«dataclass»")
    pat = PATTERN.get(c["name"])
    head = '<div class="hd">%s<div class="nm %s">%s</div>%s</div>' % (
        '<div class="st">%s</div>' % " ".join(stereo) if stereo else "",
        "it" if c["kind"] in ("interface", "abstract") else "", html.escape(c["name"]),
        '<div class="pat">%s</div>' % html.escape(pat) if pat else "")
    if simple:
        keep = OVERVIEW_METHODS.get(c["name"], [])
        meths = [m for m in c["methods"] if m["name"] in keep]
        body = "".join('<div class="m%s">+ %s()</div>' % (" it" if m["abstract"] else "", html.escape(m["name"])) for m in meths)
        return '<div class="box" id="b-%s">%s%s</div>' % (c["name"], head, '<div class="sec">%s</div>' % body if body else "")
    if compact:
        return '<div class="box" id="b-%s">%s</div>' % (c["name"], head)
    attrs = "".join('<div class="%s">%s %s</div>' % ("u" if a["static"] else "", a["vis"], html.escape(a["text"])) for a in c["attrs"][:12])
    meths = "".join('<div class="%s">%s %s</div>' % (" ".join(x for x in ("it" if m["abstract"] else "", "u" if m["static"] else "") if x),
                                                    m["vis"], html.escape(m["text"])) for m in c["methods"])
    return '<div class="box" id="b-%s">%s<div class="sec">%s</div><div class="sec">%s</div><div class="file">%s</div></div>' % (
        c["name"], head, attrs or "&nbsp;", meths or "&nbsp;", html.escape(c["file"]))


def render(pages: list[dict], classes: dict, title: str, path: Path) -> None:
    parts = []
    for i, p in enumerate(pages):
        names = [n for row in p["rows"] for n in row]
        missing = [n for n in names if n not in classes]
        if missing:
            raise SystemExit("unknown classes on page %r: %s" % (p["title"], missing))
        edges = list(p["edges"])
        for n in names:   # automatic inheritance arrows for classes on the same page
            for b in classes[n]["bases"]:
                if b in names and (n, b, "inherit", "") not in edges:
                    edges.append((n, b, "inherit", ""))
        rows = "".join('<div class="row">%s</div>' % "".join(
            box_html(classes[n], p.get("simple", False), p.get("compact", False) or n in p.get("brief", ())) for n in row) for row in p["rows"])
        parts.append('<section class="page" data-edges=\'%s\'><div class="inner"><h1>%s</h1><p class="note">%s</p>{{LEGEND}}<div class="canvas"><svg class="wires"></svg>%s</div></div></section>'
                     % (html.escape(json.dumps(edges)), html.escape(p["title"]), html.escape(p["note"]), rows))
    legend = ('<div class="legend"><b>Legend</b> '
              '<span>── ▷ inherits / implements</span><span>──→ has (field)</span>'
              '<span>- - → uses</span><span>- - → «create» creates</span>'
              '<span><i>italic</i> = abstract</span><span><u>underline</u> = static / class-level</span>'
              '<span>+ public, - private</span><span>purple tag = design pattern</span></div>')
    doc = TEMPLATE.replace("{{TITLE}}", html.escape(title)).replace("{{PAGES}}", "".join(parts)).replace("{{LEGEND}}", legend)
    path.write_text(doc)


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{TITLE}}</title>
<style>
:root{--ink:#111827;--muted:#6b7280;--line:#374151;--box:#ffffff;--head:#eef2ff;--pat:#6d28d9;--bg:#ffffff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:10px/1.35 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.page{width:277mm;height:190mm;margin:0 auto;overflow:hidden;page-break-after:always;break-after:page;position:relative}
.inner{transform-origin:top left;padding:2mm 2mm 0}
h1{font-size:15px;margin:0 0 2px}
.note{margin:0 0 8px;color:#374151;font-size:10.5px;max-width:250mm}
.canvas{position:relative}
.row{display:flex;z-index:1;justify-content:center;align-items:flex-start;gap:26px 30px;flex-wrap:wrap;margin-bottom:34px;position:relative;z-index:1}
.box{background:var(--box);border:1.3px solid var(--line);border-radius:3px;min-width:120px;max-width:300px;font-family:Menlo,Consolas,monospace;font-size:8.6px}
.hd{background:var(--head);text-align:center;padding:4px 6px;border-bottom:1.3px solid var(--line)}
.st{font-size:8px;color:var(--muted)} .nm{font-weight:700;font-size:10.5px;font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.pat{display:inline-block;margin-top:2px;padding:0 5px;border-radius:3px;background:#ede9fe;color:var(--pat);font-size:8px;font-family:-apple-system,"Segoe UI",Arial,sans-serif;font-weight:600}
.sec{padding:3px 6px;border-bottom:1px solid #d1d5db;overflow-wrap:anywhere} .sec:last-child{border-bottom:none}
.file{padding:1px 6px;color:var(--muted);font-size:7.5px;text-align:right;border-top:1px solid #d1d5db}
.it{font-style:italic} .u{text-decoration:underline}
svg.wires{position:absolute;left:0;top:0;width:100%;height:100%;z-index:0;pointer-events:none;overflow:visible}
.legend{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:9px;color:#374151;margin:0 0 6px}
.simple .box{font-size:10px;min-width:150px} .simple .nm{font-size:12px} .simple .row{gap:30px 40px;margin-bottom:46px}
@page{size:A4 landscape;margin:8mm}
@media screen{body{background:#e5e7eb;padding:8px 0}.page{background:#fff;margin:12px auto;box-shadow:0 1px 4px rgba(0,0,0,.15)}}
</style></head><body class="{{BODYCLASS}}">
{{PAGES}}
<script>
function fit(page){
  const inner=page.querySelector('.inner'); const W=page.clientWidth, H=page.clientHeight; let sc=1;
  for(let i=0;i<12;i++){
    inner.style.width=(W/sc)+'px'; inner.style.transform='scale('+sc+')';
    const h=inner.scrollHeight*sc; if(h<=H) break;
    sc=sc*Math.max(0.6, Math.sqrt(H/h))*0.99;
  }
}
function pos(el, root){ let x=0,y=0; while(el && el!==root){ x+=el.offsetLeft; y+=el.offsetTop; el=el.offsetParent; } return [x,y]; }
function draw(){
 document.querySelectorAll('.page').forEach(page=>{
  fit(page);
  const canvas=page.querySelector('.canvas'), svg=page.querySelector('svg.wires');
  const edges=JSON.parse(page.dataset.edges); const NS='http://www.w3.org/2000/svg';
  svg.innerHTML='<defs>'+
   '<marker id="tri" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="11" markerHeight="11" orient="auto-start-reverse"><path d="M0,0 L12,6 L0,12 z" fill="#fff" stroke="#374151" stroke-width="1.2"/></marker>'+
   '<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10" fill="none" stroke="#374151" stroke-width="1.4"/></marker></defs>';
  const rect=id=>{const e=page.querySelector('#b-'+id);if(!e)return null;const [x,y]=pos(e,canvas);const w=e.offsetWidth,h=e.offsetHeight;return{x,y,w,h,cx:x+w/2,cy:y+h/2};};
  const clip=(r,tx,ty)=>{const dx=tx-r.cx,dy=ty-r.cy;if(!dx&&!dy)return[r.cx,r.cy];const sx=dx?(r.w/2)/Math.abs(dx):1e9,sy=dy?(r.h/2)/Math.abs(dy):1e9,s=Math.min(sx,sy);return[r.cx+dx*s,r.cy+dy*s];};
  edges.forEach(([a,b,kind,label])=>{
   const A=rect(a),B=rect(b); if(!A||!B) return;
   let d;
   if(a===b){ const x=A.x+A.w, y=A.y+12; d=`M${x},${y} C${x+40},${y-10} ${x+40},${y+40} ${x},${y+28}`; }
   else { const [x1,y1]=clip(A,B.cx,B.cy), [x2,y2]=clip(B,A.cx,A.cy); d=`M${x1},${y1} L${x2},${y2}`; }
   const p=document.createElementNS(NS,'path'); p.setAttribute('d',d); p.setAttribute('fill','none'); p.setAttribute('stroke','#374151'); p.setAttribute('stroke-width','1.2');
   if(kind==='uses'||kind==='creates') p.setAttribute('stroke-dasharray','5 3');
   p.setAttribute('marker-end', kind==='inherit'?'url(#tri)':'url(#arr)'); svg.appendChild(p);
   const text=kind==='creates'?('«create»'+(label?' '+label:'')):label;
   if(text){ const L=p.getTotalLength(), pt=p.getPointAtLength(L*0.5);
    const t=document.createElementNS(NS,'text'); t.setAttribute('x',pt.x+4); t.setAttribute('y',pt.y-3); t.setAttribute('font-size','8.5'); t.setAttribute('fill','#1f2937');
    t.setAttribute('paint-order','stroke'); t.setAttribute('stroke','#fff'); t.setAttribute('stroke-width','3'); t.textContent=text; svg.appendChild(t); }
  });
 });
}
window.addEventListener('load',draw); window.addEventListener('beforeprint',draw);
</script></body></html>"""


if __name__ == "__main__":
    classes = read_classes()
    t = TEMPLATE
    TEMPLATE = t.replace("{{BODYCLASS}}", "simple")
    render(OVERVIEW_PAGES, classes, "LoRa Forum class diagram overview", OUT / "class-diagram-overview.html")
    TEMPLATE = t.replace("{{BODYCLASS}}", "")
    render(FULL_PAGES, classes, "LoRa Forum class diagrams", OUT / "class-diagrams.html")
    print("wrote", OUT / "class-diagrams.html", "and", OUT / "class-diagram-overview.html", "(%d classes read)" % len(classes))
