from flask import Flask, render_template, request, redirect, url_for
import json
import os
from datetime import datetime
import math

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load labs.json (list of lab panels)
def load_labs():
    with open(os.path.join(BASE_DIR, "labs.json"), "r", encoding="utf-8") as f:
        return json.load(f)

# Utility: find lab by id (case-insensitive)
def find_lab(lab_id):
    labs = load_labs()
    return next((l for l in labs if l["id"].lower() == str(lab_id).lower()), None)

# Home / search page
@app.route("/", methods=["GET"])
def index():
    labs = load_labs()
    # we pass list of {id, panel_name} for dropdown
    return render_template("index.html", labs=labs, current_year=datetime.now().year)

# Show selected panel and form for entering values
@app.route("/panel", methods=["POST"])
def panel():
    lab_id = request.form.get("lab_id")
    if not lab_id:
        return redirect(url_for("index"))

    lab = find_lab(lab_id)
    if not lab:
        return render_template("index.html", labs=load_labs(),
                               error=f"No panel found for '{lab_id}'", current_year=datetime.now().year)

    # render result template with lab (no interpretations yet)
    return render_template("result.html", lab=lab, interpretations=None, summary=None,
                           age=request.form.get("age",""), gender=request.form.get("gender",""),
                           current_year=datetime.now().year)

# Interpret submitted values
@app.route("/interpret", methods=["POST"])
def interpret():
    lab_id = request.form.get("lab_id")
    lab = find_lab(lab_id)
    if not lab:
        return render_template("index.html", labs=load_labs(),
                               error="Selected panel not found", current_year=datetime.now().year)

    age = request.form.get("age", "")
    gender = request.form.get("gender", "").lower()

    interpretations = []
    abnormal_any = False

    # For each test in panel, look up posted value by test id
    for test in lab.get("tests", []):
        tid = test.get("id")
        name = test.get("name")
        units = test.get("units","")
        raw = request.form.get(tid, "").strip()
        value = None
        if raw != "":
            try:
                value = float(raw)
                if math.isfinite(value) is False:
                    value = None
            except Exception:
                value = None

        # determine normal range numeric parsing where possible
        normal_str = test.get("normal_range", "")
        low, high = None, None
        # handle formats like "Male: 13.5-17.5, Female: 12-15.5" or "80-96" or "135-145"
        try:
            s = normal_str
            # choose gender-specific part if present
            if ("Male:" in s or "Female:" in s) and gender in ("male","female"):
                parts = [p.strip() for p in s.split(",")]
                for p in parts:
                    if gender == "male" and p.lower().startswith("male"):
                        rng = p.split(":",1)[1].strip()
                        low, high = [float(x.strip()) for x in rng.replace("–","-").split("-")[:2]]
                        break
                    if gender == "female" and p.lower().startswith("female"):
                        rng = p.split(":",1)[1].strip()
                        low, high = [float(x.strip()) for x in rng.replace("–","-").split("-")[:2]]
                        break
            if low is None:
                # try to parse first numeric range
                rng = s.replace("–","-")
                if "-" in rng:
                    # take first pair of numbers
                    nums = [x.strip() for x in rng.split("-")]
                    # attempt to extract numbers from strings (remove text)
                    low = float(''.join(ch for ch in nums[0] if (ch.isdigit() or ch in ".")).strip())
                    high = float(''.join(ch for ch in nums[1] if (ch.isdigit() or ch in ".")).strip())
        except Exception:
            low, high = None, None

        status = "Not entered"
        detail = ""
        if value is None:
            status = "Not entered"
        else:
            if low is not None and high is not None:
                if value < low:
                    status = "Low"
                    detail = test.get("low_causes","")
                    abnormal_any = True
                elif value > high:
                    status = "High"
                    detail = test.get("high_causes","")
                    abnormal_any = True
                else:
                    status = "Normal"
            else:
                status = "Value entered"
        interpretations.append({
            "id": tid,
            "name": name,
            "value": value,
            "units": units,
            "normal_range": normal_str,
            "status": status,
            "detail": detail
        })

    # Clinical summary rules (basic, extendable)
    summary = generate_summary(lab["id"].lower(), interpretations, age, gender)

    return render_template("result.html", lab=lab, interpretations=interpretations, summary=summary,
                           age=age, gender=gender, current_year=datetime.now().year)

def generate_summary(lab_id, interpretations, age, gender):
    # simple heuristic summaries for each panel
    abn = {i["name"]: i for i in interpretations if i["status"] in ("Low","High")}
    # CBC heuristics
    if lab_id == "cbc":
        hb = abn.get("Hemoglobin") or next((i for i in interpretations if i["name"]=="Hemoglobin"), None)
        mcv = abn.get("Mean Corpuscular Volume (MCV)") or next((i for i in interpretations if i["name"].startswith("Mean Corpuscular Volume")), None)
        wbc = abn.get("White Blood Cells") or next((i for i in interpretations if i["name"].startswith("White")), None)
        plt = abn.get("Platelet Count") or next((i for i in interpretations if "Platelet" in i["name"]), None)

        parts = []
        if hb and hb["value"] is not None and hb["status"]=="Low":
            if mcv and mcv["value"] is not None:
                if mcv["value"] < 80:
                    parts.append("Microcytic anemia — consider iron deficiency or thalassemia.")
                elif mcv["value"] > 100:
                    parts.append("Macrocytic anemia — consider B12/folate deficiency.")
                else:
                    parts.append("Normocytic anemia — consider acute blood loss or chronic disease.")
            else:
                parts.append("Anemia (low hemoglobin) — correlate with MCV for classification.")
        if wbc and wbc["value"] is not None and wbc["status"]=="High":
            parts.append("Leukocytosis — possible infection or inflammation.")
        if plt and plt["value"] is not None and plt["status"]=="Low":
            parts.append("Thrombocytopenia — consider viral infection, DIC, or marrow suppression.")
        if parts:
            return " ".join(parts)
        return "CBC: no major abnormalities detected."

    # RFT heuristics
    if lab_id == "rft":
        creat = next((i for i in interpretations if i["name"]=="Serum Creatinine" or i["id"]=="creatinine"), None)
        urea = next((i for i in interpretations if "Urea" in i["name"] or i["id"]=="urea"), None)
        na = next((i for i in interpretations if "Sodium" in i["name"] or i["id"]=="na"), None)
        k = next((i for i in interpretations if "Potassium" in i["name"] or i["id"]=="k"), None)
        parts = []
        if creat and creat["value"] is not None and creat["status"]=="High":
            parts.append("Raised creatinine — suggests renal impairment or AKI; correlate clinically.")
        if urea and urea["value"] is not None and urea["status"]=="High":
            parts.append("High urea — consider dehydration, renal impairment or high protein intake.")
        if na and na["value"] is not None and na["status"]!="Normal":
            parts.append("Sodium abnormality — check volume status and medications.")
        if k and k["value"] is not None and k["status"]!="Normal":
            parts.append("Potassium abnormality — beware of arrhythmia risk if severe.")
        if parts:
            return " ".join(parts)
        return "RFT: no major abnormalities detected."

    # LFT heuristics
    if lab_id == "lft":
        alt = next((i for i in interpretations if "ALT" in i["name"] or i["id"]=="alt"), None)
        ast = next((i for i in interpretations if "AST" in i["name"] or i["id"]=="ast"), None)
        alb = next((i for i in interpretations if "Albumin" in i["name"] or i["id"]=="alb"), None)
        parts = []
        if alt and alt["value"] is not None and alt["status"]=="High":
            parts.append("Raised ALT/AST — suggests hepatocellular injury (e.g., hepatitis, drug toxicity).")
        if alb and alb["value"] is not None and alb["status"]=="Low":
            parts.append("Low albumin — consider chronic liver disease or protein loss.")
        if parts:
            return " ".join(parts)
        return "LFT: no major abnormalities detected."

    # TFT heuristics
    if lab_id == "tft":
        tsh = next((i for i in interpretations if i["name"]=="TSH" or i["id"]=="tsh"), None)
        t3 = next((i for i in interpretations if "T3" in i["name"] or i["id"]=="t3"), None)
        t4 = next((i for i in interpretations if "T4" in i["name"] or i["id"]=="t4"), None)
        if tsh and tsh["value"] is not None:
            if tsh["status"]=="High":
                return "Likely hypothyroidism (high TSH). Correlate with free T4."
            if tsh["status"]=="Low":
                return "Likely hyperthyroidism (suppressed TSH). Correlate with free T4/T3."
        return "TFT: no major abnormalities detected."

        # ABG heuristics
    if lab_id == "abg":
        ph = next((i for i in interpretations if i["name"] == "pH" or i["id"] == "ph"), None)
        pco2 = next((i for i in interpretations if "pCO" in i["name"] or i["id"] in ["pco2", "pco₂"]), None)
        hco3 = next((i for i in interpretations if "HCO" in i["name"] or i["id"] in ["hco3", "hco₃"]), None)
        parts = []

        # Safety checks
        if not ph or not pco2 or not hco3 or None in (ph["value"], pco2["value"], hco3["value"]):
            return "ABG: incomplete data — please enter values for pH, pCO₂, and HCO₃⁻."

        ph_val = ph["value"]
        pco2_val = pco2["value"]
        hco3_val = hco3["value"]

        # Normal reference ranges
        normal_pco2 = (35, 45)
        normal_hco3 = (22, 26)

        # Identify disturbance
        if ph_val < 7.35:  # Acidemia
            if pco2_val > normal_pco2[1] and hco3_val >= normal_hco3[0]:
                parts.append("Respiratory Acidosis — likely due to hypoventilation (e.g., COPD, CNS depression).")
            elif hco3_val < normal_hco3[0] and pco2_val <= normal_pco2[1]:
                parts.append("Metabolic Acidosis — consider diabetic ketoacidosis, renal failure, or lactic acidosis.")
            elif pco2_val > normal_pco2[1] and hco3_val < normal_hco3[0]:
                parts.append("Mixed Acidosis — both respiratory and metabolic components present.")
            else:
                parts.append("Acidemia — unclassified pattern; check for mixed disorder.")
        elif ph_val > 7.45:  # Alkalemia
            if pco2_val < normal_pco2[0] and hco3_val <= normal_hco3[1]:
                parts.append("Respiratory Alkalosis — likely due to hyperventilation (anxiety, hypoxia, sepsis).")
            elif hco3_val > normal_hco3[1] and pco2_val >= normal_pco2[0]:
                parts.append("Metabolic Alkalosis — may result from vomiting, diuretics, or bicarbonate excess.")
            elif pco2_val < normal_pco2[0] and hco3_val > normal_hco3[1]:
                parts.append("Mixed Alkalosis — combined respiratory and metabolic causes.")
            else:
                parts.append("Alkalemia — unclear pattern, consider mixed disorder.")
        else:
            if (pco2_val > normal_pco2[1] and hco3_val > normal_hco3[1]) or (pco2_val < normal_pco2[0] and hco3_val < normal_hco3[0]):
                parts.append("Compensated disorder — pH normal but opposing changes in pCO₂ and HCO₃⁻ suggest compensation.")
            else:
                parts.append("Normal ABG — no major acid-base disturbance detected.")

        return " ".join(parts)

    # default
    return "No focused summary available for this panel."

if __name__ == "__main__":
    app.run(debug=True)
