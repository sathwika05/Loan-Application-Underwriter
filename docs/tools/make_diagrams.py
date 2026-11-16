"""Generate docs/diagrams/*.png.

Run from repo root:  backend\\.venv\\Scripts\\python docs\\tools\\make_diagrams.py
Needs Graphviz (dot) on PATH or at C:\\Program Files\\Graphviz\\bin.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "diagrams"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))
os.environ["PATH"] = r"C:\Program Files\Graphviz\bin" + os.pathsep + os.environ["PATH"]

import graphviz  # noqa: E402


# ---------- 1. LangGraph: exported from the compiled graph ----------
def langgraph_png():
    from app.graph import ROUTE_GRAPH

    g = ROUTE_GRAPH.get_graph()
    notes = {
        "load_application": "lock row; stop if already assigned",
        "find_eligible": "hard rules + exclusion reasons",
        "score_rules": "weighted deterministic score",
        "genai_optimize": "OpenAI call (limit 1)\n±0.10 deltas + rationale",
        "guard": "clamp, shortlist only,\nrules fallback",
        "assign_and_notify": "assignment + audit +\n2 notifications (1 txn)",
        "queue": "status QUEUED + reasons",
    }
    dot = graphviz.Digraph("langgraph", format="png")
    dot.attr(rankdir="TB", dpi="160", fontname="Helvetica", nodesep="0.35", ranksep="0.35",
             label="Routing graph (exported from compiled LangGraph)", labelloc="t", fontsize="13")
    dot.attr("node", fontname="Helvetica", fontsize="11", shape="box", style="rounded,filled",
             fillcolor="#eef2ff", color="#4f46e5")
    dot.attr("edge", fontname="Helvetica", fontsize="9", color="#334155")
    for nid in g.nodes:
        if nid in ("__start__", "__end__"):
            dot.node(nid, nid.strip("_").upper(), shape="oval", fillcolor="#e2e8f0", color="#64748b")
        elif nid == "genai_optimize":
            dot.node(nid, f"{nid}\n{notes[nid]}", fillcolor="#fef3c7", color="#b45309")
        else:
            dot.node(nid, f"{nid}\n{notes.get(nid, '')}")
    for e in g.edges:
        label = str(e.data) if e.data else ""
        dot.edge(e.source, e.target, label=label, style="dashed" if e.conditional else "solid")
    dot.render(OUT / "langgraph", cleanup=True)


# ---------- 2. ER diagram: generated from SQLAlchemy models ----------
def er_png():
    from sqlalchemy import Index

    from app.db import Base
    import app.models  # noqa: F401  (registers tables)

    dot = graphviz.Digraph("er", format="png")
    dot.attr(rankdir="TB", dpi="160", fontname="Helvetica", nodesep="0.25", ranksep="0.45")
    dot.attr("node", shape="plaintext", fontname="Helvetica", fontsize="11")
    for table in Base.metadata.sorted_tables:
        uniques = {c.name for c in table.columns if c.unique}
        for idx in table.indexes:
            if isinstance(idx, Index) and idx.unique:
                uniques |= {c.name for c in idx.columns}
        rows = []
        for c in table.columns:
            tags = []
            if c.primary_key:
                tags.append("PK")
            if c.foreign_keys:
                tags.append("FK")
            if c.name in uniques:
                tags.append("UQ*" if c.name == "application_id" else "UQ")
            if not c.nullable and not c.primary_key:
                tags.append("NN")
            rows.append(f'<tr><td align="left">{c.name}</td><td align="left"><font color="#475569">'
                        f'{str(c.type).split("(")[0].lower()}</font></td>'
                        f'<td align="left">{"<b>" + " ".join(tags) + "</b>" if tags else "&nbsp;"}</td></tr>')
        html = (f'<<table border="1" cellborder="0" cellspacing="0" cellpadding="3" color="#4f46e5">'
                f'<tr><td colspan="3" bgcolor="#e0e7ff"><b>{table.name}</b></td></tr>{"".join(rows)}</table>>')
        dot.node(table.name, html)
    # Two rows: parents on top, children below (keeps the image wide enough to read at page width).
    with dot.subgraph() as top:
        top.attr(rank="same")
        for t in ("underwriter", "loan_application", "routing_weights"):
            top.node(t)
    with dot.subgraph() as bottom:
        bottom.attr(rank="same")
        for t in ("assignment", "outcome", "audit_event", "notification"):
            bottom.node(t)
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            one_to_one = any(c.unique for c in fk.parent.table.columns if c.name == fk.parent.name)
            dot.edge(parent, table.name, label=f"{'1 : 1' if one_to_one else '1 : N'}\n{fk.parent.name}",
                     fontsize="11", arrowhead="tee" if one_to_one else "crow", arrowtail="tee", dir="both",
                     constraint="false" if parent == "assignment" else "true")
    dot.attr(label="PK primary key · FK foreign key · NN not null · UQ unique · "
                   "UQ* partial unique index (one active assignment per application)",
             labelloc="b", fontsize="11")
    dot.render(OUT / "er-diagram", cleanup=True)


# ---------- 3. AWS production design (not implemented) ----------
def aws_png():
    from diagrams import Cluster, Diagram, Edge
    from diagrams.aws.compute import ECS
    from diagrams.aws.database import Aurora
    from diagrams.aws.integration import SQS, SNS
    from diagrams.aws.management import Cloudwatch
    from diagrams.aws.network import ALB, CloudFront
    from diagrams.aws.security import SecretsManager, Cognito
    from diagrams.aws.storage import S3
    from diagrams.onprem.client import Users
    from diagrams import Node
    from diagrams.onprem.network import Internet

    Node._height = 2.5  # default 1.9 leaves no room below the icon for the larger labels
    graph_attr = {"fontsize": "30", "dpi": "110", "pad": "0.2", "nodesep": "0.7", "ranksep": "0.6",
                  "labelloc": "t"}
    node_attr = {"fontsize": "19"}
    edge_attr = {"fontsize": "22"}
    cl = {"fontsize": "22"}
    with Diagram("Production design, not implemented", filename=str(OUT / "aws-architecture"),
                 outformat="png", show=False, direction="TB", graph_attr=graph_attr, node_attr=node_attr,
                 edge_attr=edge_attr):
        users = Users("Submitters /\nmanagers")
        with Cluster("Edge", graph_attr=cl):
            cdn = CloudFront("CloudFront")
            web = S3("React build")
            auth = Cognito("Cognito")
        with Cluster("VPC (private subnets)", graph_attr=cl):
            alb = ALB("ALB")
            with Cluster("ECS Fargate", graph_attr=cl):
                api = ECS("FastAPI API")
                worker = ECS("LangGraph\nworker")
            queue = SQS("SQS + DLQ")
            db = Aurora("Aurora PG\n+ pgvector")
        with Cluster("Shared services", graph_attr=cl):
            secrets = SecretsManager("Secrets Manager")
            logs = Cloudwatch("CloudWatch")
            notify = SNS("SNS / SES")
        llm = Internet("OpenAI API\n(external)")

        users >> cdn >> web
        users >> Edge(label="sign-in") >> auth
        cdn >> alb >> api >> Edge(label="enqueue") >> queue >> worker
        api >> db
        worker >> db
        worker >> Edge(label="1 call / run") >> llm
        worker >> Edge(style="dotted") >> [secrets, logs, notify]


if __name__ == "__main__":
    langgraph_png()
    er_png()
    aws_png()
    for p in sorted(OUT.glob("*.png")):
        print(p.name, p.stat().st_size, "bytes")
