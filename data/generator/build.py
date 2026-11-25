"""Seeded generator for the synthetic dataset. Everything here is synthetic.

Reproducibility rules: integer PRNG only (rng.py); money in whole rupees (int); rates in basis points (int);
EMI maths in Decimal; per-customer random streams; no clock, no environment, no set/dict-order dependence.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, getcontext

from .rng import Rng
from .schema import TABLES

getcontext().prec = 40

SEED = 20261125
AS_OF = date(2026, 11, 1)
WIN_START = date(2025, 11, 1)  # BoB transaction window: 12 months to 2026-10-31
STMT_START = date(2026, 5, 1)  # external statement window for NTB: 6 months
LAST_DAY = AS_OF - timedelta(days=1)
NOTICE = "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT"

SEGMENTS = {"ETB_EST": 440, "ETB_THIN": 80, "ETB_NTC": 80, "NTB_EST": 250, "NTB_THIN": 50, "NTB_NTC": 100}
HIST = {"EST": "ESTABLISHED", "THIN": "THIN", "NTC": "NO_HISTORY"}
THIN_SCORE_PRESENT = {"ETB": 48, "NTB": 30}  # of 80 and 50: 78 of 130 present, 52 null (exact)

THIN_PAT = {"THIN_ONE_8M", "THIN_CARD_ONLY", "THIN_CLOSED_LOAN", "THIN_SECURED_CARD", "THIN_BOB_ONLY"}
NTC_PAT = {"NTC_YOUNG", "NTC_SE_IRREG", "NTC_PENSIONER"}
CONFLICTS = {t: THIN_PAT - {t} for t in THIN_PAT}
CONFLICTS.update({t: NTC_PAT - {t} for t in NTC_PAT})
CONFLICTS.update({
    "AGE_UNDER_21": {"NTC_YOUNG", "NTC_PENSIONER", "AGE_57_62", "BORDERLINE_FOIR", "GOVT_SALARY_BOB", "STAFF"},
    "AGE_57_62": {"NTC_YOUNG", "AGE_UNDER_21", "JOB_UNDER_1Y"},
    "BORDERLINE_FOIR": {"INCOME_MISMATCH_15", "SALARY_IRREGULAR", "AGE_UNDER_21", "WRITTEN_OFF_OR_SETTLED"},
    "JOB_UNDER_1Y": {"NTC_PENSIONER", "AGE_57_62", "GOVT_SALARY_BOB"},
    "GOVT_SALARY_BOB": {"NTC_YOUNG", "NTC_SE_IRREG", "NTC_PENSIONER", "AGE_UNDER_21", "JOB_UNDER_1Y"},
    "STAFF": {"NRI"},
})
# (tag, count, pool(rel, hist)); order matters: smallest pools first
TAGS = [
    ("THIN_BOB_ONLY", 10, lambda r, h: r == "ETB" and h == "THIN"),
    ("THIN_ONE_8M", 30, lambda r, h: h == "THIN"),
    ("THIN_CARD_ONLY", 25, lambda r, h: h == "THIN"),
    ("THIN_CLOSED_LOAN", 15, lambda r, h: h == "THIN"),
    ("THIN_SECURED_CARD", 15, lambda r, h: h == "THIN"),
    ("THIN_DPD", 15, lambda r, h: h == "THIN"),
    ("NTC_YOUNG", 60, lambda r, h: h == "NO_HISTORY"),
    ("NTC_SE_IRREG", 40, lambda r, h: h == "NO_HISTORY"),
    ("NTC_PENSIONER", 30, lambda r, h: h == "NO_HISTORY"),
    ("STAFF", 20, lambda r, h: r == "ETB"),
    ("NRI", 12, lambda r, h: r == "ETB"),
    ("NRI", 18, lambda r, h: r == "NTB"),
    ("KYC_EXPIRED_PENDING", 30, lambda r, h: r == "ETB"),
    ("DORMANT_ACCOUNT", 30, lambda r, h: r == "ETB"),
    ("GOVT_SALARY_BOB", 50, lambda r, h: r == "ETB"),
    ("RETURNED_EMI", 25, lambda r, h: r == "ETB" and h == "ESTABLISHED"),
    ("WRITTEN_OFF_OR_SETTLED", 15, lambda r, h: h == "ESTABLISHED"),
    ("LATE_DPD30_EST", 80, lambda r, h: h == "ESTABLISHED"),
    ("BORDERLINE_FOIR", 60, lambda r, h: h == "ESTABLISHED"),
    ("AGE_UNDER_21", 8, lambda r, h: True),
    ("AGE_57_62", 25, lambda r, h: True),
    ("JOB_UNDER_1Y", 90, lambda r, h: True),
    ("SALARY_IRREGULAR", 70, lambda r, h: True),
    ("INCOME_MISMATCH_15", 80, lambda r, h: True),
    ("NAME_VARIANT", 50, lambda r, h: True),
    ("DUP_MOBILE", 12, lambda r, h: True),
]

FIRST_M = ["Aarav", "Vihaan", "Arjun", "Rohan", "Karthik", "Suresh", "Ramesh", "Manoj", "Deepak", "Vikram", "Nikhil", "Sanjay", "Amit", "Rahul", "Ajay", "Harish", "Naveen", "Pranav", "Sandeep", "Gaurav", "Mohan", "Anil", "Kiran", "Yash", "Tarun", "Imran", "Faisal", "Gurpreet", "Jaspreet", "Joseph"]
FIRST_F = ["Ananya", "Diya", "Priya", "Sneha", "Kavya", "Meera", "Lakshmi", "Pooja", "Neha", "Divya", "Sunita", "Anjali", "Shweta", "Rekha", "Swati", "Nandini", "Isha", "Aditi", "Ritu", "Shalini", "Farah", "Harpreet", "Mary", "Bhavna", "Tanvi", "Komal", "Radhika", "Preeti", "Jyoti", "Asha"]
LAST = ["Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Mehta", "Gupta", "Singh", "Kumar", "Das", "Banerjee", "Chatterjee", "Joshi", "Kulkarni", "Deshmukh", "Pillai", "Menon", "Rao", "Naidu", "Khan", "Ansari", "Chopra", "Malhotra", "Bhatt", "Trivedi", "Yadav", "Mishra", "Dubey"]
STREETS = ["MG Road", "Station Road", "Gandhi Nagar", "Nehru Colony", "Shivaji Park", "Lake View Apartments", "Temple Street", "Market Road", "Sunrise Residency", "Green Park"]
MERCHANTS = ["Fresh Mart", "City Super Bazaar", "Metro Pharmacy", "Quick Bites", "Tea Point", "Fuel Station", "Cab Ride", "Online Mart", "Mobile Recharge", "Book Store", "Dosa Corner", "Garage Services"]
EXT_BANKS = ["State Bank of India", "HDFC Bank", "ICICI Bank", "Axis Bank", "Kotak Mahindra Bank", "Punjab National Bank", "Canara Bank", "IndusInd Bank"]
EMPLOYERS = {
    "GOVT": ["State Education Department", "Municipal Corporation", "Public Works Department", "State Health Department", "Postal Services Department"],
    "PSU": ["National Power Utility", "State Transport Corporation", "Heavy Engineering PSU", "Oil and Gas PSU", "Steel Works PSU"],
    "MNC": ["Northwind Technologies", "Contoso Global Services", "Fabrikam Systems", "Globex Consulting", "Initech Software", "Hooli Cloud India"],
    "LISTED": ["Sunrise Pharma Ltd", "Bharat Auto Components Ltd", "Deccan Textiles Ltd", "Ganga Cement Ltd", "Lotus Foods Ltd"],
    "PRIVATE_LTD": ["Orbit Logistics Pvt Ltd", "Pinnacle Infra Pvt Ltd", "Zenith Retail Pvt Ltd", "Kaveri Agro Pvt Ltd", "Maple IT Services Pvt Ltd", "Vega Healthcare Pvt Ltd"],
    "OTHER": ["Sai Enterprises", "City Hospital Trust", "Greenfield School Society", "Metro Traders", "Anand Clinic"],
}
DESIGNATIONS = {"SALARIED_PRIVATE": ["Software Engineer", "Sales Executive", "Accountant", "Operations Manager", "Analyst", "Team Lead", "HR Executive", "Technician"],
                "SALARIED_GOVT": ["Clerk", "Teacher", "Section Officer", "Inspector", "Junior Engineer"],
                "SALARIED_PSU": ["Assistant Manager", "Engineer", "Technician", "Officer"],
                "SELF_EMPLOYED_PROFESSIONAL": ["Consultant", "Chartered Accountant", "Doctor", "Architect"],
                "SELF_EMPLOYED_BUSINESS": ["Proprietor", "Partner", "Trader", "Contractor"],
                "PENSIONER": ["Retired Government Officer", "Retired Teacher", "Retired Engineer"]}
LENDERS = {"PSU_BANK": ["State Bank of India", "Canara Bank", "Punjab National Bank", "Union Bank of India"],
           "PRIVATE_BANK": ["HDFC Bank", "ICICI Bank", "Axis Bank", "Kotak Mahindra Bank", "IndusInd Bank"],
           "NBFC": ["Bajaj Finance", "Tata Capital", "Muthoot Finance", "Aditya Birla Finance", "Hero FinCorp"],
           "FINTECH": ["QuickCash Digital", "PaySmart Credit"],
           "CARD_ISSUER": ["HDFC Bank Cards", "SBI Card", "ICICI Bank Cards", "Axis Bank Cards"]}
BRANCHES = (
    [(n, c, s, "METRO") for n, c, s in [("Andheri", "Mumbai", "Maharashtra"), ("Dadar", "Mumbai", "Maharashtra"), ("Connaught Place", "Delhi", "Delhi"), ("Karol Bagh", "Delhi", "Delhi"), ("Indiranagar", "Bengaluru", "Karnataka"), ("Koramangala", "Bengaluru", "Karnataka"), ("T Nagar", "Chennai", "Tamil Nadu"), ("Salt Lake", "Kolkata", "West Bengal"), ("Banjara Hills", "Hyderabad", "Telangana"), ("Kothrud", "Pune", "Maharashtra"), ("Navrangpura", "Ahmedabad", "Gujarat"), ("Sector 14", "Gurugram", "Haryana")]]
    + [(n, c, s, "URBAN") for n, c, s in [("Malviya Nagar", "Jaipur", "Rajasthan"), ("Hazratganj", "Lucknow", "Uttar Pradesh"), ("Adajan", "Surat", "Gujarat"), ("Alkapuri", "Vadodara", "Gujarat"), ("Vijay Nagar", "Indore", "Madhya Pradesh"), ("MP Nagar", "Bhopal", "Madhya Pradesh"), ("Dharampeth", "Nagpur", "Maharashtra"), ("RS Puram", "Coimbatore", "Tamil Nadu"), ("MG Road", "Kochi", "Kerala"), ("Sector 17", "Chandigarh", "Chandigarh"), ("Boring Road", "Patna", "Bihar"), ("Dwaraka Nagar", "Visakhapatnam", "Andhra Pradesh")]]
    + [(n, c, s, "SEMI_URBAN") for n, c, s in [("College Road", "Nashik", "Maharashtra"), ("Race Course", "Rajkot", "Gujarat"), ("Vijayanagar", "Mysuru", "Karnataka"), ("City Palace Road", "Udaipur", "Rajasthan"), ("Napier Town", "Jabalpur", "Madhya Pradesh"), ("KK Nagar", "Madurai", "Tamil Nadu"), ("Brodipet", "Guntur", "Andhra Pradesh"), ("Rajpur Road", "Dehradun", "Uttarakhand")]]
    + [(n, c, s, "RURAL") for n, c, s in [("Main Bazaar", "Dholka", "Gujarat"), ("Main Road", "Karad", "Maharashtra"), ("Station Road", "Sehore", "Madhya Pradesh"), ("Court Road", "Bundi", "Rajasthan"), ("Market Road", "Hassan", "Karnataka"), ("Bus Stand Road", "Theni", "Tamil Nadu"), ("Civil Lines", "Unnao", "Uttar Pradesh"), ("Old Town", "Ropar", "Punjab")]]
)
LOAN_SPEC = {  # product: ((min, max, step) amount, (min, max) bp, (min, max) tenure months)
    "PERSONAL": ((50000, 1000000, 5000), (1050, 1900), (12, 60)),
    "HOME": ((1000000, 8000000, 50000), (800, 950), (120, 240)),
    "AUTO": ((300000, 1500000, 10000), (850, 1200), (36, 84)),
    "EDUCATION": ((200000, 1500000, 10000), (950, 1300), (48, 120)),
    "GOLD": ((50000, 500000, 5000), (900, 1300), (6, 24)),
    "CONSUMER_DURABLE": ((15000, 200000, 1000), (1200, 1800), (6, 18)),
}
TL_TO_PRODUCT = {"PERSONAL_LOAN": "PERSONAL", "HOME_LOAN": "HOME", "AUTO_LOAN": "AUTO", "EDUCATION_LOAN": "EDUCATION", "GOLD_LOAN": "GOLD", "CONSUMER_LOAN": "CONSUMER_DURABLE"}
PCT = [(0, 15000), (10, 22000), (30, 32000), (50, 45000), (70, 65000), (90, 110000), (98, 220000), (100, 350000)]


# ---------------------------------------------------------------- helpers
def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, 28))


def rnd(x: int, step: int = 500) -> int:
    return (x + step // 2) // step * step


def income_from_pct(p: int) -> int:
    for (p0, v0), (p1, v1) in zip(PCT, PCT[1:]):
        if p <= p1:
            return v0 + (v1 - v0) * (p - p0) // (p1 - p0)
    return PCT[-1][1]


def emi_of(p: int, bp: int, n: int) -> int:
    r = Decimal(bp) / Decimal(120000)
    f = (1 + r) ** n
    return int((Decimal(p) * r * f / (f - 1)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def outstanding(p: int, bp: int, n: int, k: int) -> int:
    """Balance after k scheduled instalments."""
    r = Decimal(bp) / Decimal(120000)
    f = (1 + r) ** k
    bal = Decimal(p) * f - Decimal(emi_of(p, bp, n)) * (f - 1) / r
    return max(0, int(bal.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def age_on(dob: date, on: date = AS_OF) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def month_idx(d: date) -> int | None:
    """Index 0..23 into monthly_dpd_24m (23 = most recent complete month), or None if outside."""
    m = (AS_OF.year * 12 + AS_OF.month) - (d.year * 12 + d.month)
    return 24 - m if 1 <= m <= 24 else None


def size_loan(r: Rng, product: str, net: int, cap_pct: int = 35, fill: bool = False):
    """Sample a loan. `fill` sizes it up to the affordability cap (used so borderline-FOIR customers carry a meaningful EMI)."""
    (lo, hi, step), (bl, bh), (tl, th) = LOAN_SPEC[product]
    bp = r.randint(bl, bh)
    n = r.randint(tl, th)
    p = rnd(r.randint(lo, hi), step)
    emi = emi_of(p, bp, n)
    cap = net * cap_pct // 100
    if emi > cap or fill:  # keep a single loan affordable
        p = min(hi, max(lo, rnd(p * cap // emi, step)))
        emi = emi_of(p, bp, n)
    return p, bp, n, emi


# ---------------------------------------------------------------- context
class Ctx:
    def __init__(self):
        self.n: dict[str, int] = {}
        self.t = {ds: {t: [] for t in TABLES[ds]} for ds in TABLES}

    def nid(self, key: str, prefix: str, width: int) -> str:
        self.n[key] = self.n.get(key, 0) + 1
        return f"{prefix}-{self.n[key]:0{width}d}"

    def add(self, ds: str, table: str, row: dict) -> dict:
        self.t[ds][table].append(row)
        return row


def assign_tags(root: Rng, custs: list[dict]) -> None:
    def clash(a: str, b: str) -> bool:
        return b in CONFLICTS.get(a, ()) or a in CONFLICTS.get(b, ())

    for name, count, pool in TAGS:
        elig = [c for c in custs if pool(c["rel"], c["hist"]) and not any(clash(name, t) for t in c["tags"])]
        if len(elig) < count:
            raise RuntimeError(f"tag {name}: only {len(elig)} eligible, need {count}")
        for c in root.sample(elig, count):
            c["tags"].add(name)


# ---------------------------------------------------------------- per-customer pieces
def other_kinds(r: Rng, c: dict, n: int) -> list[str]:
    kinds = [r.weighted([("PERSONAL_LOAN", 18), ("HOME_LOAN", 10), ("AUTO_LOAN", 12), ("CREDIT_CARD", 35), ("CONSUMER_LOAN", 10), ("GOLD_LOAN", 5), ("EDUCATION_LOAN", 4), ("OVERDRAFT", 6)]) for _ in range(n)]
    if "BORDERLINE_FOIR" in c["tags"] and not any(k in TL_TO_PRODUCT and k not in ("GOLD_LOAN",) for k in kinds) and kinds:
        kinds[0] = "PERSONAL_LOAN"
    return kinds


def mk_other_tl(ctx: Ctx, r: Rng, cid: str, kind: str, net: int, *, opened: date, closed: date | None = None, big: bool = False) -> dict:
    ltype = "CARD_ISSUER" if "CARD" in kind else r.weighted([("PSU_BANK", 25), ("PRIVATE_BANK", 35), ("NBFC", 30), ("FINTECH", 10)])
    row = {"tradeline_id": ctx.nid("tl", "TL", 6), "customer_id": cid, "lender_name": r.choice(LENDERS[ltype]), "lender_type": ltype, "is_bob": False,
           "existing_loan_id": None, "account_type": kind, "ownership": "JOINT" if r.chance(5) else "INDIVIDUAL", "opened_date": opened, "closed_date": closed,
           "sanctioned_amount": None, "credit_limit": None, "current_balance": 0, "emi_amount": None, "status": "CLOSED" if closed else "ACTIVE",
           "max_dpd": 0, "monthly_dpd_24m": [0] * 24}
    if kind in TL_TO_PRODUCT:
        prod = TL_TO_PRODUCT[kind]
        p, bp, n, emi = size_loan(r, prod, net, fill=big)
        if not closed and (AS_OF - opened).days // 30 >= n - 1:
            # an active loan cannot be older than its own tenure: start it later instead of stretching the tenure
            opened = AS_OF - timedelta(days=r.randint(45, max(46, (n - 1) * 30)))
            row["opened_date"] = opened
        elapsed = max(1, (AS_OF - opened).days // 30)
        if closed:
            n = max(1, min(n, (closed - opened).days // 30 or 1))
            emi = emi_of(p, bp, n)
        row.update(sanctioned_amount=p, emi_amount=emi, current_balance=0 if closed else outstanding(p, bp, n, min(elapsed, n - 1)))
        row["emi_amount"] = emi_of(p, bp, n)
    elif kind in ("CREDIT_CARD", "SECURED_CREDIT_CARD"):
        lim = rnd(r.randint(30000, 150000 if kind == "SECURED_CREDIT_CARD" else 500000), 5000)
        row.update(credit_limit=lim, current_balance=0 if closed else rnd(lim * r.randint(0, 60) // 100, 100))
    else:  # OVERDRAFT
        lim = rnd(r.randint(50000, 500000), 5000)
        row.update(credit_limit=lim, current_balance=rnd(lim * r.randint(0, 70) // 100, 100))
    return row


def exists_in_month(tl: dict, m_back: int) -> bool:
    d = add_months(AS_OF, -m_back)
    return tl["opened_date"] <= d and (tl["closed_date"] is None or tl["closed_date"] >= d)


def put_dpd(r: Rng, tl: dict, values: list[int], lo: int, hi: int) -> None:
    months = [m for m in range(lo, hi + 1) if exists_in_month(tl, m)]
    if not months:
        months = [1]
    for v, m in zip(values, r.sample(months, min(len(values), len(months)))):
        tl["monthly_dpd_24m"][24 - m] = v


def finalize_tl(tl: dict) -> None:
    tl["max_dpd"] = max(tl["monthly_dpd_24m"])
    if tl["status"] == "ACTIVE" and tl["monthly_dpd_24m"][23] > 0:
        tl["status"] = "OVERDUE"


def make_bob_loan(ctx: Ctx, r: Rng, cid: str, acct: dict, net: int, product: str, *, closed: bool = False, recent_months: int | None = None,
                  min_elapsed: int = 2) -> tuple[dict, list[dict]]:
    p, bp, n, emi = size_loan(r, product, net)
    lo_start = acct["opened_on"] + timedelta(days=31)  # a loan never starts before its repayment account exists
    avail = (AS_OF - lo_start).days // 30
    if closed:
        n = min(n, 24)
        if avail < n + 3:
            closed = False
        else:
            emi = emi_of(p, bp, n)
            start = add_months(AS_OF, -(n + r.randint(2, min(18, avail - n))))
    if recent_months:
        start = AS_OF - timedelta(days=recent_months * 30 + r.randint(0, 20))
        n = max(n, recent_months + 4)
        emi = emi_of(p, bp, n)
    elif not closed:
        n = max(n, min_elapsed + 2)
        emi = emi_of(p, bp, n)
        start = add_months(AS_OF, -r.randint(min_elapsed, min(n - 1, 48, max(min_elapsed, avail))))
    start = start.replace(day=r.randint(1, 28))
    start = max(start, lo_start)
    loan = {"loan_id": ctx.nid("loan", "LN", 5), "customer_id": cid, "product_type": product, "sanctioned_amount": p, "outstanding_amount": 0, "emi_amount": emi,
            "interest_rate": bp, "tenure_months": n, "start_date": start, "maturity_date": add_months(start, n), "status": "CLOSED" if closed else "ACTIVE",
            "repayment_account_id": acct["account_id"], "_n": n}
    reps, k = [], 1
    while add_months(start, k) <= LAST_DAY and k <= n:
        d = add_months(start, k)
        reps.append({"repayment_id": ctx.nid("rep", "RP", 6), "loan_id": loan["loan_id"], "due_date": d, "due_amount": emi, "paid_date": d, "paid_amount": emi,
                     "days_past_due": 0, "status": "PAID_ON_TIME"})
        k += 1
    if not reps:  # started too recently for any instalment: shift earlier
        raise AssertionError("loan without instalments")
    return loan, reps


def late(rep: dict, days: int) -> bool:
    d = rep["due_date"] + timedelta(days=days)
    if d > LAST_DAY:
        return False
    rep.update(paid_date=d, days_past_due=days, status="PAID_LATE")
    return True


def miss(reps: list[dict], count: int) -> None:
    for rep in reps[-count:]:
        rep.update(paid_date=None, paid_amount=None, days_past_due=(AS_OF - rep["due_date"]).days, status="MISSED")


def bob_tradeline(ctx: Ctx, loan: dict, reps: list[dict]) -> dict:
    arr = [0] * 24
    for rep in reps:
        i = month_idx(rep["due_date"])
        if i is not None:
            arr[i] = max(arr[i], rep["days_past_due"] // 30 * 30)
    missed = sum(1 for x in reps if x["status"] == "MISSED")
    paid_k = len(reps) - missed
    loan["outstanding_amount"] = 0 if loan["status"] == "CLOSED" else outstanding(loan["sanctioned_amount"], loan["interest_rate"], loan["_n"], paid_k)
    if missed and loan["status"] == "ACTIVE":
        loan["status"] = "OVERDUE"
    tl = {"tradeline_id": ctx.nid("tl", "TL", 6), "customer_id": loan["customer_id"], "lender_name": "Bank of Baroda", "lender_type": "PSU_BANK", "is_bob": True,
          "existing_loan_id": loan["loan_id"], "account_type": next(k for k, v in TL_TO_PRODUCT.items() if v == loan["product_type"]), "ownership": "INDIVIDUAL",
          "opened_date": loan["start_date"], "closed_date": loan["maturity_date"] if loan["status"] == "CLOSED" else None, "sanctioned_amount": loan["sanctioned_amount"],
          "credit_limit": None, "current_balance": loan["outstanding_amount"], "emi_amount": loan["emi_amount"], "status": loan["status"], "max_dpd": 0, "monthly_dpd_24m": arr}
    tl["status"] = "CLOSED" if loan["status"] == "CLOSED" else ("OVERDUE" if loan["status"] == "OVERDUE" else "ACTIVE")
    return tl


# ---------------------------------------------------------------- ledger
def month_starts(first: date) -> list[date]:
    out, d = [], first
    while d < AS_OF:
        out.append(d)
        d = add_months(d, 1)
    return out


def ledger(r: Rng, *, first: date, emp: dict, prev_emp: dict | None, net: int, observed: int, tags: set, opening: int, bob_emis: list[dict],
           other_tls: list[dict], inst_label: str) -> list[dict]:
    """Return ledger entries in posting order with running balances. Debits that would overdraw are preceded by a
    TRANSFER_IN top-up, so balances never go negative."""
    ev: list[dict] = []
    seq = [0]

    def add(d, amt, direction, cat, chan, narr, cp=None, status="POSTED", loan_id=None, tl_id=None):
        if d < first or d >= AS_OF or amt <= 0:
            return
        seq[0] += 1
        ev.append({"d": d, "o": 0 if direction == "CREDIT" else 1, "q": seq[0], "amt": amt, "dir": direction, "cat": cat, "chan": chan, "narr": narr,
                   "cp": cp, "st": status, "loan": loan_id, "tl": tl_id})

    etype = emp["employment_type"]
    pay_day = r.randint(1, 7)
    skip: set[int] = set()
    months = month_starts(first)
    if "SALARY_IRREGULAR" in tags:
        skip = set(r.sample(range(len(months)), min(len(months) // 4 + 1, 3)))
    rent = net >= 25000 and r.chance(35)
    sip = r.chance(25)
    card = r.chance(30)
    fixed_pm = 0
    rent_amt = rnd(net * r.randint(12, 22) // 100, 500) if rent else 0
    sip_amt = rnd(net * r.randint(5, 10) // 100, 100) if sip else 0
    for mi, m in enumerate(months):
        # credits
        income_m = 0
        if etype in ("SALARIED_PRIVATE", "SALARIED_GOVT", "SALARIED_PSU"):
            before = m < emp["start_date"].replace(day=1)
            who = prev_emp if (before and prev_emp is not None) else emp
            amt = observed if who is emp else prev_emp["declared_monthly_net"]
            if before and prev_emp is None:
                amt = 0  # no earlier employer on record: no salary before the current job started
            elif mi in skip:
                amt = 0
            elif "SALARY_IRREGULAR" in tags and r.chance(35):
                amt = amt * r.randint(55, 85) // 100
            amt = rnd(amt, 100) if amt else 0
            add(m.replace(day=min(28, pay_day + r.randint(0, 2))), amt, "CREDIT", "SALARY", "NEFT", f"NEFT/SAL/{who['employer_name'][:24]}/{m:%b%y}".upper(), who["employer_name"])
            income_m = amt
        elif etype == "PENSIONER":
            amt = 0 if mi in skip else observed
            add(m.replace(day=r.randint(1, 3)), amt, "CREDIT", "PENSION", "NEFT", f"PENSION CREDIT {m:%b%y}".upper(), "Pension Disbursing Authority")
            income_m = amt
        else:
            lo, hi = (30, 180) if "SALARY_IRREGULAR" in tags or "NTC_SE_IRREG" in tags else (70, 140)
            target = observed * r.randint(lo, hi) // 100
            k = r.randint(5, 12)
            cuts = sorted(r.randint(1, 1000) for _ in range(k - 1))
            parts = [b - a for a, b in zip([0] + cuts, cuts + [1000])]
            for part in parts:
                add(m.replace(day=r.randint(1, 28)), rnd(target * part // 1000, 100), "CREDIT", "BUSINESS_RECEIPT", r.choice(["UPI", "NEFT", "IMPS"]), f"RECEIPT/{r.choice(MERCHANTS)[:20]}".upper(), None)
            income_m = target
        # fixed outflows
        if rent_amt:
            add(m.replace(day=r.randint(3, 8)), rent_amt, "DEBIT", "RENT", "UPI", "RENT PAYMENT", "Landlord")
        if sip_amt:
            add(m.replace(day=10), sip_amt, "DEBIT", "INVESTMENT", "NACH", "SIP MUTUAL FUND", "Mutual Fund")
        emi_pm = 0
        for tl in other_tls:
            if exists_in_month(tl, (AS_OF.year * 12 + AS_OF.month) - (m.year * 12 + m.month)) and tl["emi_amount"] and tl["via"]:
                add(m.replace(day=5), tl["emi_amount"], "DEBIT", "EMI", "NACH", f"NACH/EMI/{tl['lender_name'][:20]}".upper(), tl["lender_name"], tl_id=tl["tradeline_id"])
                emi_pm += tl["emi_amount"]
        emi_pm += sum(b["amount"] for b in bob_emis if b["date"].year == m.year and b["date"].month == m.month)
        fixed_pm = rent_amt + sip_amt + emi_pm
        # households still spend on essentials when EMIs are heavy; any shortfall is covered by a top-up transfer below
        budget = max(net * 12 // 100, max(income_m, net // 3) * 88 // 100 - fixed_pm)
        # discretionary spending, scaled to the budget
        items = []
        for _ in range(r.randint(10, 22)):
            items.append(("UPI", r.weighted([((30, 300), 45), ((300, 1500), 35), ((1500, 6000), 17), ((6000, 20000), 3)])))
        for _ in range(r.randint(1, 3)):
            items.append(("ATM", (1000, 10000)))
        for _ in range(r.randint(2, 4) if card else 0):
            items.append(("CARD", (500, 15000)))
        for _ in range(r.randint(2, 3)):
            items.append(("UTILITY", (300, 3500)))
        raw = [r.randint(lo_, hi_) for _, (lo_, hi_) in items]
        total = sum(raw) or 1
        for (kind, _), a in zip(items, raw):
            a = max(10, a * budget // total) if total > budget else a
            a = rnd(a, 500) if kind == "ATM" else a
            d = m.replace(day=r.randint(1, 28))
            if kind == "UPI":
                mer = r.choice(MERCHANTS)
                add(d, a, "DEBIT", "UPI", "UPI", f"UPI/DR/{r.randint(100000000, 999999999)}/{mer}".upper(), mer)
            elif kind == "ATM":
                add(d, a, "DEBIT", "ATM", "ATM", "ATM CASH WITHDRAWAL", None)
            elif kind == "CARD":
                mer = r.choice(MERCHANTS)
                add(d, a, "DEBIT", "CARD", "CARD", f"POS/{mer}".upper(), mer)
            else:
                add(d, a, "DEBIT", "UTILITY", "UPI", "UTILITY BILL PAYMENT", "Utility Provider")
        if m.month % 3 == 0:
            add(date(m.year, m.month, 28), r.randint(118, 472), "DEBIT", "CHARGES", "BRANCH", "SERVICE CHARGES", None)
    for qd in (date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30)):
        add(qd, r.randint(80, 1800), "CREDIT", "INTEREST", "BRANCH", "INTEREST CREDIT", None)
    # BoB loan instalments (exact amounts and dates from the repayment schedule)
    for b in bob_emis:
        add(b["date"], b["amount"], "DEBIT", "EMI", "NACH", f"NACH/EMI/BANK OF BARODA/{b['loan_id']}", "Bank of Baroda", status=b["status"], loan_id=b["loan_id"])
    ev.sort(key=lambda e: (e["d"], e["o"], e["q"]))
    out, bal = [], opening
    for e in ev:
        if e["dir"] == "DEBIT" and e["st"] == "POSTED" and e["amt"] > bal:
            top = e["amt"] - bal + rnd(r.randint(500, 5000), 100)
            bal += top
            out.append({"d": e["d"], "amt": top, "dir": "CREDIT", "cat": "TRANSFER_IN", "chan": "IMPS", "narr": "SELF TRANSFER FROM OTHER BANK", "cp": None, "st": "POSTED", "loan": None, "tl": None, "bal": bal})
        if e["st"] == "POSTED":
            bal += e["amt"] if e["dir"] == "CREDIT" else -e["amt"]
        out.append({**e, "bal": bal})
    return out


# ---------------------------------------------------------------- build
QDATES = (date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30))


def finish_account(acct: dict, entries: list[dict], opening: int) -> None:
    acct["current_balance"] = entries[-1]["bal"] if entries else opening
    me = []
    for mo in range(5, 11):
        last_d = add_months(date(2026, mo, 1), 1) - timedelta(days=1)
        prior = [e["bal"] for e in entries if e["d"] <= last_d]
        me.append(prior[-1] if prior else opening)
    acct["avg_balance_6m"] = sum(me) // 6


def secondary_entries(r: Rng, opening: int) -> list[dict]:
    ev = [(qd, r.randint(40, 700), "INTEREST", "BRANCH", "INTEREST CREDIT") for qd in QDATES]
    ev += [(WIN_START + timedelta(days=r.randint(0, 364)), rnd(r.randint(500, 8000), 100), "TRANSFER_IN", "IMPS", "TRANSFER IN") for _ in range(r.randint(2, 6))]
    ev.sort(key=lambda x: (x[0], x[2], x[1]))
    out, bal = [], opening
    for d, a, cat, ch, nar in ev:
        bal += a
        out.append({"d": d, "amt": a, "dir": "CREDIT", "cat": cat, "chan": ch, "narr": nar, "cp": None, "st": "POSTED", "loan": None, "tl": None, "bal": bal})
    return out


def variant_name(r: Rng, first: str, last: str) -> str:
    return r.choice([f"{first[0]}. {last}", f"{first} {last[0]}.", f"{first}{first[-1]} {last}"])


def build(seed: int = SEED) -> tuple[dict, dict]:
    root = Rng(seed)
    ctx = Ctx()

    branch_rows = []
    for i, (name, city, state, tier) in enumerate(BRANCHES, start=1):
        br = Rng.labelled(seed, f"branch:{i}")
        branch_rows.append(ctx.add("lh_raw", "branches", {"branch_id": f"BRN-{i:03d}", "branch_name": f"{city} {name}", "city": city, "state": state,
                           "pincode": str(br.randint(100001, 899999)), "city_tier": tier, "ifsc_code": f"BARB0SYN{i:03d}"}))
    by_tier = {t: [b for b in branch_rows if b["city_tier"] == t] for t in ("METRO", "URBAN", "SEMI_URBAN", "RURAL")}

    seg_list = [s for s, n in SEGMENTS.items() for _ in range(n)]
    root.shuffle(seg_list)
    genders = root.shuffle(["M"] * 580 + ["F"] * 410 + ["O"] * 10)
    custs = []
    for i, seg in enumerate(seg_list):
        rel, h = seg.split("_")
        custs.append({"i": i, "id": f"BOB-C{i + 1:04d}", "seg": seg, "rel": rel, "hist": HIST[h], "tags": set(), "gender": genders[i]})
    assign_tags(root, custs)
    for rel, n_present in THIN_SCORE_PRESENT.items():
        thin = [c for c in custs if c["rel"] == rel and c["hist"] == "THIN"]
        present = {c["id"] for c in root.sample(thin, n_present)}
        for c in thin:
            c["score_present"] = c["id"] in present
    kyc_pool = root.shuffle(["VALID"] * 970 + ["INVALID"] * 5 + ["NOT_FOUND"] * 15 + ["DEACTIVATED"] * 10)
    aadhaar_pool = root.shuffle(["VERIFIED"] * 950 + ["MISMATCH"] * 30 + ["NOT_CHECKED"] * 20)
    addr_pool = root.shuffle([True] * 940 + [False] * 60)
    mobiles: set[str] = set()

    for c in custs:
        r = Rng.labelled(seed, c["id"])
        cid, rel, hist, tags = c["id"], c["rel"], c["hist"], c["tags"]
        # ---- persona
        if "AGE_UNDER_21" in tags:
            age = r.randint(18, 20)
        elif "NTC_PENSIONER" in tags:
            age = r.randint(58, 62)
        elif "AGE_57_62" in tags:
            age = r.randint(57, 62)
        elif "NTC_YOUNG" in tags:
            age = r.randint(21, 26)
        elif hist == "NO_HISTORY":
            age = r.weighted([(r.randint(21, 26), 45), (r.randint(27, 35), 35), (r.randint(36, 50), 15), (r.randint(51, 56), 5)])
        elif hist == "THIN":
            age = r.randint(22, 45)
        else:
            age = r.weighted([(r.randint(22, 30), 22), (r.randint(31, 40), 33), (r.randint(41, 50), 28), (r.randint(51, 56), 17)])
        dob = date(AS_OF.year - age, AS_OF.month, AS_OF.day) - timedelta(days=r.randint(0, 364))
        first = r.choice(FIRST_M if c["gender"] == "M" else FIRST_F if c["gender"] == "F" else FIRST_M + FIRST_F)
        last = r.choice(LAST)
        while True:
            mobile = str(r.randint(6, 9)) + f"{r.randint(0, 999999999):09d}"
            if mobile not in mobiles:
                mobiles.add(mobile)
                break
        c.update(first=first, last=last, name=f"{first} {last}", age=age, dob=dob, mobile=mobile)
        adult_from = dob + timedelta(days=365 * 18 + 5)

        # ---- employment and income
        etype = r.weighted([("SALARIED_PRIVATE", 52), ("SALARIED_GOVT", 12), ("SALARIED_PSU", 6), ("SELF_EMPLOYED_PROFESSIONAL", 8), ("SELF_EMPLOYED_BUSINESS", 17), ("PENSIONER", 5)])
        if "NTC_PENSIONER" in tags:
            etype = "PENSIONER"
        elif "NTC_SE_IRREG" in tags:
            etype = r.choice(["SELF_EMPLOYED_PROFESSIONAL", "SELF_EMPLOYED_BUSINESS"])
        elif "GOVT_SALARY_BOB" in tags:
            etype = "SALARIED_GOVT"
        elif "AGE_UNDER_21" in tags or "NTC_YOUNG" in tags or etype == "PENSIONER":
            etype = "SALARIED_PRIVATE"
        net = rnd(income_from_pct(r.randint(0, 99)), 500)
        if etype == "PENSIONER":
            net = rnd(r.randint(22000, 60000), 500)
        elif "NTC_YOUNG" in tags:
            net = max(15000, rnd(net * 65 // 100, 500))
        elif "AGE_UNDER_21" in tags:
            net = rnd(r.randint(15000, 22000), 500)
        cat = {"SALARIED_GOVT": "GOVT", "SALARIED_PSU": "PSU", "PENSIONER": r.choice(["GOVT", "PSU"])}.get(etype) or (
            "PROPRIETORSHIP" if etype.startswith("SELF") else r.weighted([("MNC", 25), ("LISTED", 15), ("PRIVATE_LTD", 40), ("OTHER", 20)]))
        ename = f"{last} {r.choice(['Traders', '& Associates', 'Works', 'Consultants'])}" if cat == "PROPRIETORSHIP" else r.choice(EMPLOYERS[cat])
        if "JOB_UNDER_1Y" in tags:
            start = AS_OF - timedelta(days=r.randint(30, 330))
        else:
            start = AS_OF - timedelta(days=r.randint(400, 365 * 15))
        start = max(start, adult_from)
        gross = rnd(net * (100 if etype == "PENSIONER" else 125 if etype.startswith("SELF") else 118) // 100, 100)
        emp = {"employment_id": ctx.nid("emp", "EMP", 5), "customer_id": cid, "employment_type": etype, "employer_name": ename, "employer_category": cat,
               "designation": r.choice(DESIGNATIONS[etype]), "start_date": start, "end_date": None, "is_current": True, "declared_monthly_gross": gross, "declared_monthly_net": net}
        prev = None
        if etype.startswith("SALARIED") and ("JOB_UNDER_1Y" in tags or r.chance(30)) and start - timedelta(days=800) > adult_from:
            pcat = r.choice(["MNC", "LISTED", "PRIVATE_LTD", "OTHER"])
            pnet = max(15000, rnd(net * 80 // 100, 500))
            pend = start - timedelta(days=r.randint(0, 45))
            prev = {"employment_id": ctx.nid("emp", "EMP", 5), "customer_id": cid, "employment_type": "SALARIED_PRIVATE", "employer_name": r.choice(EMPLOYERS[pcat]),
                    "employer_category": pcat, "designation": r.choice(DESIGNATIONS["SALARIED_PRIVATE"]), "start_date": max(adult_from, start - timedelta(days=r.randint(800, 2200))),
                    "end_date": pend, "is_current": False, "declared_monthly_gross": rnd(pnet * 118 // 100, 100), "declared_monthly_net": pnet}

        # ---- account shell (ETB: Bank of Baroda; NTB: other bank)
        branch = None
        since = None
        if rel == "ETB":
            branch = r.choice(by_tier[r.weighted([("METRO", 35), ("URBAN", 30), ("SEMI_URBAN", 20), ("RURAL", 15)])])
            since = AS_OF - timedelta(days=r.randint(120, 1100) if hist == "NO_HISTORY" else r.randint(400, 3600) if hist == "THIN" else r.randint(800, 365 * 18))
            if age <= 20:
                since = AS_OF - timedelta(days=r.randint(120, 150))
            since = max(since, adult_from)
            ptype = "PENSION" if etype == "PENSIONER" else "SALARY" if etype.startswith("SALARIED") and r.chance(80) else "CURRENT" if etype == "SELF_EMPLOYED_BUSINESS" and r.chance(50) else "SAVINGS"
            if "GOVT_SALARY_BOB" in tags:
                ptype = "SALARY"
            acct = {"account_id": ctx.nid("acc", "ACC", 6), "institution_name": "Bank of Baroda", "is_bob": True, "branch_id": branch["branch_id"], "account_type": ptype,
                    "opened_on": since + timedelta(days=r.randint(0, 20)), "status": "ACTIVE", "is_salary_account": ptype == "SALARY"}
        else:
            acct = {"account_id": ctx.nid("acc", "ACC", 6), "institution_name": r.choice(EXT_BANKS), "is_bob": False, "branch_id": None,
                    "account_type": "SALARY" if etype.startswith("SALARIED") else "PENSION" if etype == "PENSIONER" else "CURRENT" if etype == "SELF_EMPLOYED_BUSINESS" else "SAVINGS",
                    "opened_on": AS_OF - timedelta(days=r.randint(400, 365 * 12)), "status": "ACTIVE", "is_salary_account": etype.startswith("SALARIED")}
        acct.update(current_balance=0, avg_balance_6m=0, currency="INR")

        # ---- credit: tradelines and Bank of Baroda loans
        tls: list[dict] = []
        loans: list[tuple[dict, list[dict]]] = []
        if hist == "ESTABLISHED":
            n_total = r.randint(3, 8)
            n_bob = 0
            if rel == "ETB":
                n_bob = r.weighted([(0, 45), (1, 40), (2, 15)])
                if "RETURNED_EMI" in tags:
                    n_bob = max(n_bob, 1)
                n_bob = min(n_bob, n_total - 1)
            sizing_net = max(net, 40000) if "BORDERLINE_FOIR" in tags else net
            big_left = "BORDERLINE_FOIR" in tags
            for j, kind in enumerate(other_kinds(r, c, n_total - n_bob)):
                old = AS_OF - timedelta(days=r.randint(400 if j == 0 else 150, 4000))
                cl = None
                if kind in TL_TO_PRODUCT and j > 0 and r.chance(25) and "BORDERLINE_FOIR" not in tags:
                    cl = old + timedelta(days=r.randint(365, 1000))
                    if cl >= AS_OF - timedelta(days=30):
                        cl = None
                big = big_left and kind in TL_TO_PRODUCT and kind != "GOLD_LOAN"
                big_left = big_left and not big
                tls.append(mk_other_tl(ctx, r, cid, kind, sizing_net, opened=old, closed=cl, big=big))
            for j in range(n_bob):
                prod = r.weighted([("PERSONAL", 30), ("HOME", 25), ("AUTO", 20), ("EDUCATION", 5), ("GOLD", 10), ("CONSUMER_DURABLE", 10)])
                ret = "RETURNED_EMI" in tags and j == 0
                loans.append(make_bob_loan(ctx, r, cid, acct, net, prod, closed=r.chance(15) and not ret, min_elapsed=8 if ret else 2))
        elif hist == "THIN":
            pat = next(iter(sorted(tags & THIN_PAT)), "THIN_DEFAULT")
            if pat == "THIN_BOB_ONLY":
                loans.append(make_bob_loan(ctx, r, cid, acct, net, r.choice(["PERSONAL", "AUTO", "CONSUMER_DURABLE"]), recent_months=r.randint(3, 10)))
            elif pat == "THIN_ONE_8M":
                tls.append(mk_other_tl(ctx, r, cid, r.choice(["PERSONAL_LOAN", "CONSUMER_LOAN"]), net, opened=AS_OF - timedelta(days=r.randint(220, 270))))
            elif pat == "THIN_CARD_ONLY":
                tls.append(mk_other_tl(ctx, r, cid, "CREDIT_CARD", net, opened=AS_OF - timedelta(days=r.randint(60, 330))))
            elif pat == "THIN_SECURED_CARD":
                tls.append(mk_other_tl(ctx, r, cid, "SECURED_CREDIT_CARD", net, opened=AS_OF - timedelta(days=r.randint(60, 200))))
            elif pat == "THIN_CLOSED_LOAN":
                op = AS_OF - timedelta(days=r.randint(240, 330))
                tls.append(mk_other_tl(ctx, r, cid, "CONSUMER_LOAN", net, opened=op, closed=op + timedelta(days=r.randint(120, 200))))
            else:
                tls.append(mk_other_tl(ctx, r, cid, r.choice(["PERSONAL_LOAN", "CONSUMER_LOAN"]), net, opened=AS_OF - timedelta(days=r.randint(60, 330))))
                tls.append(mk_other_tl(ctx, r, cid, "CREDIT_CARD", net, opened=AS_OF - timedelta(days=r.randint(60, 330))))
        others = list(tls)
        returned: list[str] = []
        if "THIN_DPD" in tags:
            if others:
                put_dpd(r, others[0], [30] * r.randint(1, 2), 1, min(5, max(1, (AS_OF - others[0]["opened_date"]).days // 30 - 1)))
            else:
                reps = loans[0][1]
                if not late(reps[-3 if len(reps) >= 3 else 0], r.randint(30, 45)):
                    raise AssertionError(f"{cid}: THIN_DPD could not be placed on the Bank of Baroda loan")
        if "LATE_DPD30_EST" in tags:
            put_dpd(r, others[0], r.sample([30, 30, 60, 90], r.randint(1, 3)), 1, 24)
        if "WRITTEN_OFF_OR_SETTLED" in tags:
            t = others[-1]
            put_dpd(r, t, [90, 90, 90], 6, 20)
            if c["i"] % 2 == 0:
                t["status"] = "SETTLED"
                t["closed_date"] = max(t["opened_date"] + timedelta(days=200), AS_OF - timedelta(days=r.randint(60, 300)))
                t["current_balance"] = 0
            else:
                t["status"] = "WRITTEN_OFF"
        if "RETURNED_EMI" in tags:
            reps = loans[0][1]
            cand = [x for x in reps if date(2026, 1, 1) <= x["due_date"] <= date(2026, 9, 30)]
            for x in r.sample(cand, min(len(cand), r.randint(1, 2))):
                if late(x, r.randint(5, 20)):
                    returned.append(x["repayment_id"])
            if not returned:
                raise AssertionError(f"{cid}: no returned instalment possible")
        elif hist == "ESTABLISHED" and loans and "THIN_DPD" not in tags and loans[0][0]["status"] == "ACTIVE" and r.chance(4):
            miss(loans[0][1], r.randint(1, 3))
        for loan, reps in loans:
            tls.append(bob_tradeline(ctx, loan, reps))
        for t in tls:
            t["via"] = False
            finalize_tl(t)

        # ---- FOIR override: income follows obligations so FOIR lands in 46-54%
        if "BORDERLINE_FOIR" in tags:
            total_emi = sum(t["emi_amount"] or 0 for t in tls if t["status"] in ("ACTIVE", "OVERDUE"))
            if total_emi == 0:
                raise AssertionError(f"{cid}: BORDERLINE_FOIR without an active EMI")
            net = max(15000, rnd(total_emi * 100 // r.randint(46, 54), 500))
            emp["declared_monthly_net"] = net
            emp["declared_monthly_gross"] = rnd(net * (125 if etype.startswith("SELF") else 118) // 100, 100)
        observed = net
        if "INCOME_MISMATCH_15" in tags:
            observed = rnd(net * (r.randint(118, 140) if r.chance(50) else r.randint(60, 84)) // 100, 100)

        # ---- ledger: Bank of Baroda transactions (ETB) or external statement lines (NTB)
        for t in tls:
            if not t["is_bob"] and t["emi_amount"] and t["status"] in ("ACTIVE", "OVERDUE") and (rel == "NTB" or r.chance(60)):
                t["via"] = True
        bob_emis = []
        if rel == "ETB":
            for loan, reps in loans:
                for x in reps:
                    if x["paid_date"] and x["paid_date"] >= WIN_START:
                        if x["repayment_id"] in returned:
                            bob_emis.append({"date": x["due_date"], "amount": x["due_amount"], "loan_id": loan["loan_id"], "status": "RETURNED"})
                        bob_emis.append({"date": x["paid_date"], "amount": x["paid_amount"], "loan_id": loan["loan_id"], "status": "POSTED"})
        opening = rnd(observed * r.randint(20, 120 if hist == "NO_HISTORY" else 250) // 100, 100)
        entries = ledger(r, first=WIN_START if rel == "ETB" else STMT_START, emp=emp, prev_emp=prev, net=net, observed=observed, tags=tags, opening=opening,
                         bob_emis=bob_emis, other_tls=[t for t in tls if not t["is_bob"]], inst_label=acct["institution_name"])
        for e in entries:
            if rel == "ETB":
                ctx.add("lh_raw", "transactions", {"txn_id": ctx.nid("txn", "TXN", 7), "account_id": acct["account_id"], "txn_date": e["d"], "amount": e["amt"], "direction": e["dir"],
                        "category": e["cat"], "channel": e["chan"], "narration": e["narr"], "counterparty": e["cp"], "txn_status": e["st"], "balance_after": e["bal"],
                        "loan_id": e["loan"], "tradeline_id": e["tl"]})
            else:
                ctx.add("lh_truth", "external_statement_lines", {"line_id": ctx.nid("line", "LINE", 7), "customer_id": cid, "account_id": acct["account_id"], "tradeline_id": e["tl"],
                        "statement_month": e["d"].replace(day=1), "txn_date": e["d"], "amount": e["amt"], "direction": e["dir"], "category": e["cat"], "narration": e["narr"],
                        "balance_after": e["bal"]})
        finish_account(acct, entries, opening)
        accts = [acct]
        if rel == "ETB":
            for _ in range(r.weighted([(0, 60), (1, 40)])):
                sec = {"account_id": ctx.nid("acc", "ACC", 6), "institution_name": "Bank of Baroda", "is_bob": True, "branch_id": branch["branch_id"], "account_type": "SAVINGS",
                       "opened_on": min(acct["opened_on"] + timedelta(days=r.randint(30, 400)), WIN_START - timedelta(days=30)), "status": "ACTIVE", "is_salary_account": False,
                       "currency": "INR"}
                sec["opened_on"] = max(sec["opened_on"], acct["opened_on"])
                sopen = rnd(r.randint(5000, 60000), 100)
                sent = secondary_entries(r, sopen)
                for e in sent:
                    ctx.add("lh_raw", "transactions", {"txn_id": ctx.nid("txn", "TXN", 7), "account_id": sec["account_id"], "txn_date": e["d"], "amount": e["amt"], "direction": "CREDIT",
                            "category": e["cat"], "channel": e["chan"], "narration": e["narr"], "counterparty": None, "txn_status": "POSTED", "balance_after": e["bal"],
                            "loan_id": None, "tradeline_id": None})
                finish_account(sec, sent, sopen)
                accts.append(sec)
            if "DORMANT_ACCOUNT" in tags:
                bal = rnd(r.randint(200, 5000), 100)
                accts.append({"account_id": ctx.nid("acc", "ACC", 6), "institution_name": "Bank of Baroda", "is_bob": True, "branch_id": branch["branch_id"], "account_type": "SAVINGS",
                              "opened_on": acct["opened_on"] + timedelta(days=r.randint(10, 100)), "status": "DORMANT", "is_salary_account": False, "current_balance": bal,
                              "avg_balance_6m": bal, "currency": "INR"})
            elif r.chance(10):
                bal = rnd(r.randint(5000, 80000), 100)
                accts.append({"account_id": ctx.nid("acc", "ACC", 6), "institution_name": r.choice(EXT_BANKS), "is_bob": False, "branch_id": None, "account_type": "SAVINGS",
                              "opened_on": AS_OF - timedelta(days=r.randint(400, 3000)), "status": "ACTIVE", "is_salary_account": False, "current_balance": bal, "avg_balance_6m": bal,
                              "currency": "INR"})
        for a in accts:
            ctx.add("lh_raw", "accounts", a)
            ctx.add("lh_raw", "account_holders", {"account_id": a["account_id"], "customer_id": cid, "holder_role": "PRIMARY", "relationship_since": a["opened_on"]})
        for loan, reps in loans:
            ctx.add("lh_raw", "existing_loans", loan)
            for x in reps:
                ctx.add("lh_raw", "loan_repayments", x)
        for t in tls:
            ctx.add("lh_raw", "bureau_tradelines", t)
        for e in [emp] + ([prev] if prev else []):
            ctx.add("lh_raw", "employment", e)
        c.update(etype=etype, net=net, observed=observed, branch=branch, since=since, entries=entries, tls=tls, loans=loans, accts=accts, prev=prev, returned=returned)

    return assemble(seed, ctx, custs, branch_rows, aadhaar_pool, kyc_pool, addr_pool)


def assemble(seed, ctx, custs, branch_rows, aadhaar_pool, kyc_pool, addr_pool):
    root = Rng.labelled(seed, "assemble")
    kyc_flagged = sorted(c["id"] for c in custs if "KYC_EXPIRED_PENDING" in c["tags"])
    expired = set(kyc_flagged[:18])
    dup = sorted(c["id"] for c in custs if "DUP_MOBILE" in c["tags"])
    by_id = {c["id"]: c for c in custs}
    for a, b in zip(dup[::2], dup[1::2]):
        by_id[b]["mobile"] = by_id[a]["mobile"]
    etb = [c for c in custs if c["rel"] == "ETB"]

    for c in custs:
        r = Rng.labelled(seed, c["id"] + ":attrs")
        cid, rel, tags, i = c["id"], c["rel"], c["tags"], c["i"]
        pan_n = (i + 1) * 7919 % 10_000_000  # injective; 7919 is coprime with 10^7
        c["pan"] = f"SYNP{pan_n:07d}{chr(65 + pan_n % 26)}"  # 12 characters: can never be a real 10-character PAN
        br = c["branch"] or r.choice(branch_rows)
        if rel == "ETB":
            kst = "EXPIRED" if cid in expired else "PENDING" if cid in kyc_flagged else "VERIFIED"
            verified = None if kst == "PENDING" else max(c["since"], AS_OF - timedelta(days=r.randint(760, 1400) if kst == "EXPIRED" else r.randint(30, 700)))
        else:
            kst, verified = "NOT_APPLICABLE", None
        ctx.add("lh_raw", "customers", {
            "customer_id": cid, "bank_relationship": rel, "full_name": c["name"], "date_of_birth": c["dob"], "gender": c["gender"], "mobile": c["mobile"],
            "email": f"{c['first'].lower()}.{c['last'].lower()}{i + 1:04d}@example.invalid", "pan": c["pan"], "aadhaar_last4": f"{r.randint(0, 9999):04d}",
            "address_line": f"{r.randint(1, 250)}, {r.choice(STREETS)}", "city": br["city"], "state": br["state"], "pincode": br["pincode"],
            "residency_status": "NRI" if "NRI" in tags else "RESIDENT", "is_staff": "STAFF" in tags, "home_branch_id": c["branch"]["branch_id"] if c["branch"] else None,
            "customer_since": c["since"], "kyc_status": kst, "kyc_last_verified_on": verified})
        c["addr"] = f"{br['city']}, {br['state']}"
        # mock KYC response
        pan_status, aadhaar, addr_ok = kyc_pool[i], aadhaar_pool[i], addr_pool[i]
        variant = "NAME_VARIANT" in tags
        c["kyc_name"] = variant_name(r, c["first"], c["last"]) if variant else c["name"]
        ctx.add("lh_raw", "ext_kyc_mock", {"customer_id": cid, "pan": c["pan"], "pan_status": pan_status, "pan_name_on_record": c["kyc_name"],
                "name_match_score": r.randint(78, 95) if variant else r.randint(97, 100), "aadhaar_check": aadhaar, "address_match": addr_ok,
                "checked_at": f"2026-11-01T{r.randint(0, 23):02d}:{r.randint(0, 59):02d}:{r.randint(0, 59):02d}Z"})
        # bureau profile derived from the tradelines
        tls, hist = c["tls"], c["hist"]
        br_ = Rng.labelled(seed, cid + ":bureau")
        report = AS_OF - timedelta(days=br_.randint(0, 10))
        if hist == "NO_HISTORY":
            score = None
        else:
            worst = max((t["max_dpd"] for t in tls), default=0)
            bad = any(t["status"] in ("WRITTEN_OFF", "SETTLED") for t in tls)
            if hist == "ESTABLISHED":
                band = br_.weighted([((750, 890), 40), ((700, 749), 30), ((650, 699), 17), ((560, 649), 13)])
                score = br_.randint(*band)
                score = min(score, br_.randint(480, 580)) if bad else min(score, br_.randint(540, 620)) if worst >= 60 else min(score, br_.randint(600, 680)) if worst >= 30 else score
            else:
                score = br_.randint(600, 760) if c["score_present"] else None
                if score is not None and worst >= 30:
                    score = min(score, br_.randint(560, 640))
        active = [t for t in tls if t["status"] in ("ACTIVE", "OVERDUE")]
        ctx.add("lh_raw", "bureau_profiles", {
            "customer_id": cid, "bureau_name": "MOCK_CIBIL", "history_status": hist, "score": score, "score_date": report if score is not None else None,
            "tradeline_count": len(tls), "active_tradeline_count": len(active), "oldest_tradeline_date": min((t["opened_date"] for t in tls), default=None),
            "total_outstanding": sum(t["current_balance"] for t in active), "total_monthly_emi": sum(t["emi_amount"] or 0 for t in active),
            "enquiries_last_6m": br_.weighted([(0, 30), (1, 30), (2, 20), (3, 12), (4, 5), (5, 3)]) if hist == "ESTABLISHED" else br_.weighted([(0, 35), (1, 35), (2, 20), (3, 10)]),
            "max_dpd_last_12m": None if hist == "NO_HISTORY" else max((max(t["monthly_dpd_24m"][12:]) for t in tls), default=0),
            "has_written_off": any(t["status"] == "WRITTEN_OFF" for t in tls), "has_settled": any(t["status"] == "SETTLED" for t in tls), "report_date": report})
        c["score"] = score
        ctx.add("lh_truth", "customer_ground_truth", {
            "customer_id": cid, "segment_code": c["seg"], "edge_case_tags": sorted(tags),
            "persona_notes": f"{rel} {hist.lower().replace('_', ' ')}; {c['etype'].lower().replace('_', ' ')}; age {c['age']}; declared net {c['net']}"})

    # joint holders on about 8% of Bank of Baroda accounts
    bob_accts = [(c, a) for c in etb for a in c["accts"] if a["is_bob"] and a["status"] == "ACTIVE"]
    for c, a in root.sample(bob_accts, len(bob_accts) * 8 // 100):
        other = root.choice([x for x in etb if x["id"] != c["id"]])
        ctx.add("lh_raw", "account_holders", {"account_id": a["account_id"], "customer_id": other["id"], "holder_role": "JOINT",
                "relationship_since": max(a["opened_on"], other["since"])})

    # historical applications (past Personal Loan outcomes)
    hr = Rng.labelled(seed, "hist")
    span = (date(2026, 10, 31) - date(2024, 1, 1)).days
    for k in range(2000):
        if hr.chance(55):
            c = hr.choice(etb)
            cust, ref, rel, hist = c["id"], None, "ETB", c["hist"]
        else:
            cust, ref = None, f"APLN-{k + 1:06d}"
            rel, hist = hr.weighted([("ETB", 40), ("NTB", 60)]), hr.weighted([("ESTABLISHED", 60), ("THIN", 15), ("NO_HISTORY", 25)])
        adate = date(2024, 1, 1) + timedelta(days=hr.randint(0, span))
        req = rnd(hr.randint(50000, 1500000), 5000)
        ten = hr.choice([12, 24, 36, 48, 60])
        p = {"ESTABLISHED": 72, "THIN": 55, "NO_HISTORY": 48}[hist] + (8 if rel == "ETB" else 0)
        outcome = "WITHDRAWN" if hr.chance(5) else "APPROVED" if hr.chance(p) else "DECLINED"
        days = hr.randint(1, 8) if rel == "ETB" else hr.randint(2, 14)
        ok = outcome == "APPROVED"
        ctx.add("lh_raw", "historical_applications", {
            "application_id": f"HAPP-{k + 1:05d}", "customer_id": cust, "applicant_ref": ref, "scheme": "BARODA_PERSONAL_LOAN", "application_date": adate,
            "channel": hr.weighted([("BRANCH", 55), ("ONLINE_LEAD", 25), ("DSA", 20)]), "relationship_at_application": rel, "history_status_at_application": hist,
            "requested_amount": req, "requested_tenure_months": ten, "approved_amount": rnd(req * hr.randint(70, 100) // 100, 5000) if ok else None,
            "approved_rate": hr.randint(1100, 1750) if ok else None, "approved_tenure_months": ten if ok else None, "outcome": outcome,
            "decline_reason_code": hr.choice(["FOIR_EXCEEDED", "INCOME_NOT_VERIFIED", "ADVERSE_REPAYMENT", "AGE_OUT_OF_RANGE", "DOCUMENT_ISSUES", "OTHER"]) if outcome == "DECLINED" else None,
            "decision_date": adate + timedelta(days=days) if outcome != "WITHDRAWN" else None, "processing_days": days,
            "decided_by_role": hr.weighted([("ASSISTANT_MANAGER", 70), ("BRANCH_MANAGER", 30)]) if outcome != "WITHDRAWN" else None})

    build_documents(seed, ctx, custs)
    # strip generator-internal keys: only schema columns reach the canonical files
    for ds, tbls in ctx.t.items():
        for name, rows in tbls.items():
            cols = [col for col, _ in TABLES[ds][name][1]]
            tbls[name] = [{k: row.get(k) for k in cols} for row in rows]
    segs = {s: sum(1 for c in custs if c["seg"] == s) for s in SEGMENTS}
    thin = [c for c in custs if c["hist"] == "THIN"]
    tag_counts: dict[str, int] = {}
    for c in custs:
        for t in c["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    summary = {"segment_counts": segs, "total_customers": len(custs),
               "thin_file_scores": {"thin_total": len(thin), "score_present": sum(1 for c in thin if c["score"] is not None),
                                    "score_null": sum(1 for c in thin if c["score"] is None),
                                    "present_by_relationship": {r_: sum(1 for c in thin if c["rel"] == r_ and c["score"] is not None) for r_ in ("ETB", "NTB")}},
               "edge_case_tag_counts": dict(sorted(tag_counts.items()))}
    return ctx.t, summary


def build_documents(seed: int, ctx: Ctx, custs: list[dict]) -> None:
    dr = Rng.labelled(seed, "docs")
    chosen: list[dict] = []
    for seg in sorted(SEGMENTS):
        chosen += sorted(dr.sample([c for c in custs if c["seg"] == seg], 20), key=lambda c: c["id"])
    docs = []
    for c in chosen:
        salaried = c["etype"].startswith("SALARIED")
        for t in ("PAN_CARD", "AADHAAR_MASKED", "SALARY_SLIP" if salaried else "ITR", "BANK_STATEMENT"):
            docs.append({"c": c, "type": t, "disc": "NONE"})
    pick = lambda pool, k: dr.sample([d for d in pool if d["disc"] == "NONE"], k)  # noqa: E731
    for d in pick([x for x in docs if x["type"] in ("PAN_CARD", "AADHAAR_MASKED")], 24):
        d["disc"] = "NAME_VARIANT"
    for d in pick([x for x in docs if x["type"] == "PAN_CARD"], 12):
        d["disc"] = "PAN_TYPO"
    for d in pick([x for x in docs if x["type"] == "AADHAAR_MASKED"], 12):
        d["disc"] = "DOB_MISMATCH"
    for d in pick([x for x in docs if x["type"] in ("SALARY_SLIP", "ITR")], 24):
        d["disc"] = "INCOME_MISMATCH"
    quality = dr.shuffle(["CLEAN"] * 240 + ["SKEWED"] * 96 + ["LOW_RES"] * 72 + ["SCANNED_NOISE"] * 72)
    ext = {"CLEAN": "pdf", "SKEWED": "png", "LOW_RES": "jpg", "SCANNED_NOISE": "scan.pdf"}
    for n, d in enumerate(docs):
        c, t, disc = d["c"], d["type"], d["disc"]
        r = Rng.labelled(seed, f"doc:{n}")
        name = {"NAME_VARIANT": variant_name(r, c["first"], c["last"])}.get(disc, c["name"])
        pan = c["pan"] if disc != "PAN_TYPO" else c["pan"][:-1] + chr(65 + (ord(c["pan"][-1]) - 64) % 26)
        dob = c["dob"] if disc != "DOB_MISMATCH" else c["dob"] + timedelta(days=r.randint(31, 400))
        net = c["net"] if disc != "INCOME_MISMATCH" else rnd(c["net"] * (r.randint(118, 140) if r.chance(50) else r.randint(60, 84)) // 100, 100)
        pages, period = 1, ""
        if t == "PAN_CARD":
            f = {"name": name, "pan": pan, "dob": dob.isoformat(), "father_name": f"{r.choice(FIRST_M)} {c['last']}"}
        elif t == "AADHAAR_MASKED":
            f = {"name": name, "dob": dob.isoformat(), "gender": c["gender"], "aadhaar_masked": "XXXX XXXX " + ctx_aadhaar(ctx, c["id"]), "address": c["addr"]}
        elif t == "SALARY_SLIP":
            period = "2026-10"
            gross = rnd(net * 118 // 100, 100)
            f = {"employee_name": name, "employer": c["emp_name"] if "emp_name" in c else ctx_emp(ctx, c["id"]), "month": period, "gross": gross, "deductions": gross - net, "net": net}
        elif t == "ITR":
            period, pages = "AY 2026-27", 2
            f = {"assessment_year": "2026-27", "name": name, "pan": pan, "gross_total_income": net * 12}
        else:
            period = "2026-05..2026-10"
            ent = [e for e in c["entries"] if e["d"] >= date(2026, 5, 1)]
            first = ent[0]
            opening = first["bal"] - (first["amt"] if first["dir"] == "CREDIT" else -first["amt"] if first["st"] == "POSTED" else 0)
            posted = [e for e in ent if e["st"] == "POSTED"]
            f = {"account_holder": c["name"], "institution": c["accts"][0]["institution_name"], "account_id": c["accts"][0]["account_id"], "period": period,
                 "opening_balance": opening, "closing_balance": ent[-1]["bal"], "total_credits": sum(e["amt"] for e in posted if e["dir"] == "CREDIT"),
                 "total_debits": sum(e["amt"] for e in posted if e["dir"] == "DEBIT"),
                 "income_credit_count": sum(1 for e in posted if e["cat"] in ("SALARY", "PENSION", "BUSINESS_RECEIPT")),
                 "income_credit_total": sum(e["amt"] for e in posted if e["cat"] in ("SALARY", "PENSION", "BUSINESS_RECEIPT")),
                 "emi_debit_count": sum(1 for e in posted if e["cat"] == "EMI"), "emi_debit_total": sum(e["amt"] for e in posted if e["cat"] == "EMI")}
            pages = max(1, -(-len(ent) // 40))
        f["synthetic_notice"] = NOTICE
        did = f"DOC-{n + 1:04d}"
        ctx.add("lh_truth", "documents_truth", {"doc_id": did, "customer_id": c["id"], "doc_type": t, "period": period, "page_count": pages, "quality": quality[n],
                "expected_fields": f, "injected_discrepancy": disc, "file_name": f"{did}.{ext[quality[n]]}"})


def ctx_aadhaar(ctx: Ctx, cid: str) -> str:
    return next(r["aadhaar_last4"] for r in ctx.t["lh_raw"]["customers"] if r["customer_id"] == cid)


def ctx_emp(ctx: Ctx, cid: str) -> str:
    return next(r["employer_name"] for r in ctx.t["lh_raw"]["employment"] if r["customer_id"] == cid and r["is_current"])
