from sqlalchemy import select

from .models import Underwriter

UNDERWRITERS = [
    ("Priya Sharma", ["mortgage", "personal"], ["East", "West"], 1_500_000, 6, 20, 0.94,
     "Senior mortgage underwriter: jumbo loans, self-employed borrowers, complex income documentation."),
    ("Raj Patel", ["mortgage"], ["East", "South"], 600_000, 8, 16, 0.86,
     "Conforming mortgages, FHA/VA, first-time home buyers."),
    ("Maria Gonzalez", ["auto", "personal"], ["East", "West", "Midwest", "South"], 80_000, 12, 6, 0.90,
     "High-volume consumer lending, auto loans, thin credit files."),
    ("David Chen", ["business"], ["West", "East"], 5_000_000, 5, 36, 0.95,
     "Commercial and SBA loans, financial statement and cash-flow analysis."),
    ("Aisha Okafor", ["business", "personal"], ["Midwest", "South"], 1_000_000, 6, 28, 0.91,
     "Small business lines of credit, startups, restaurants and retail."),
    ("Tom Becker", ["mortgage", "auto"], ["Midwest"], 500_000, 8, 18, 0.84,
     "Refinance, home equity, debt consolidation, dealer auto loans."),
    ("Lena Novak", ["personal", "auto"], ["East", "Midwest"], 60_000, 10, 8, 0.85,
     "Personal loans, debt consolidation, subprime credit."),
    ("James Wright", ["mortgage", "business"], ["South", "West"], 2_000_000, 7, 22, 0.92,
     "Jumbo mortgages, investment properties, rural and agricultural borrowers."),
    ("Kenji Tanaka", ["mortgage", "personal"], ["East", "West", "Midwest", "South"], 900_000, 8, 20, 0.87,
     "Generalist mortgage underwriter (currently on leave)."),
]


def seed(db) -> None:
    if db.execute(select(Underwriter.id).limit(1)).first():
        return
    for name, types, regions, max_amt, cap, tat, q, profile in UNDERWRITERS:
        email = name.lower().replace(" ", ".") + "@bank.local"
        db.add(Underwriter(name=name, email=email, loan_types=types, regions=regions, max_amount=max_amt,
                           max_caseload=cap, avg_turnaround_hours=tat, quality_score=q, profile=profile,
                           active=name != "Kenji Tanaka"))
    db.commit()
