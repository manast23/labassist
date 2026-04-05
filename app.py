from flask import Flask, render_template, request, redirect, url_for, jsonify
import json
import os
import re
import math
import requests
from datetime import datetime

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_API_KEY = os.environ.get("OCR_SPACE_API_KEY", "")

def load_labs():
    with open(os.path.join(BASE_DIR, "labs.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def find_lab(lab_id):
    labs = load_labs()
    return next((l for l in labs if l["id"].lower() == str(lab_id).lower()), None)

# ── OCR route ──────────────────────────────────────────────────────────────────
@app.route("/ocr", methods=["POST"])
def ocr():
    if "report" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["report"]
    lab_id = request.form.get("lab_id", "")
    lab = find_lab(lab_id)
    if not lab:
        return jsonify({"error": "Panel not found"}), 400
    try:
        resp = requests.post(
            "https://api.ocr.space/parse/image",
            files={"file": (file.filename, file.stream, file.mimetype)},
            data={
                "apikey": OCR_API_KEY,
                "language": "eng",
                "isOverlayRequired": False,
                "detectOrientation": True,
                "scale": True,
                "OCREngine": 2,
            },
            timeout=30,
        )
        result = resp.json()
    except Exception as e:
        return jsonify({"error": f"OCR request failed: {str(e)}"}), 500

    if result.get("IsErroredOnProcessing"):
        msg = result.get("ErrorMessage", ["OCR failed"])[0]
        return jsonify({"error": msg}), 500

    parsed = result.get("ParsedResults", [])
    if not parsed:
        return jsonify({"error": "No text found in image"}), 500

    full_text = " ".join(p.get("ParsedText", "") for p in parsed)
    extracted = extract_values(full_text, lab)
    return jsonify({"extracted": extracted, "raw_text": full_text})


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


EXTRA_ALIASES = {
    "wbc":        ["white blood cell", "white blood", "wbc", "leukocyte", "tlc"],
    "rbc":        ["red blood cell", "red blood", "rbc", "erythrocyte"],
    "hb":         ["hemoglobin", "haemoglobin", "hgb", "hb"],
    "hct":        ["hematocrit", "haematocrit", "pcv", "hct", "packed cell"],
    "mcv":        ["mean corpuscular volume", "mcv"],
    "mch":        ["mean corpuscular hemoglobin", "mch"],
    "mchc":       ["mean corpuscular hemoglobin concentration", "mchc"],
    "rdw":        ["red cell distribution", "rdw"],
    "plt":        ["platelet", "thrombocyte", "plt"],
    "alt":        ["alt", "sgpt", "alanine"],
    "ast":        ["ast", "sgot", "aspartate"],
    "alp":        ["alkaline phosphatase", "alp"],
    "tbil":       ["total bilirubin", "bilirubin"],
    "alb":        ["albumin", "alb"],
    "urea":       ["urea", "bun", "blood urea"],
    "creatinine": ["creatinine", "creat"],
    "na":         ["sodium", "na"],
    "k":          ["potassium"],
    "cl":         ["chloride"],
    "tsh":        ["tsh", "thyroid stimulating"],
    "t3":         ["free t3", "ft3"],
    "t4":         ["free t4", "ft4"],
    "ph":         ["ph"],
    "pco2":       ["pco2", "partial pressure co"],
    "hco3":       ["hco3", "bicarbonate"],
    "po2":        ["po2", "partial pressure o"],
    "o2sat":      ["o2 sat", "spo2", "oxygen sat"],
    "glucose":    ["glucose", "blood sugar", "fbs", "rbs"],
    "protein":    ["protein"],
    "ketones":    ["ketone"],
    "urobilinogen": ["urobilinogen"],
    "specific_gravity": ["specific gravity", "sp gr"],
}


def extract_values(text, lab):
    extracted = {}
    lines = text.replace("\r", "\n").split("\n")
    aliases = {}
    for test in lab["tests"]:
        tid = test["id"]
        for kw in EXTRA_ALIASES.get(tid, []):
            aliases[normalize(kw)] = tid
        aliases[normalize(test["name"])] = tid
        aliases[normalize(tid)] = tid
        words = test["name"].split()
        if len(words) > 1:
            aliases[normalize("".join(w[0] for w in words))] = tid
    for line in lines:
        line_norm = normalize(line)
        for alias in sorted(aliases.keys(), key=len, reverse=True):
            if alias in line_norm:
                tid = aliases[alias]
                if tid in extracted:
                    break
                nums = re.findall(r"\b\d+\.?\d*\b", line)
                if nums:
                    for n in nums:
                        val = float(n)
                        if 0 < val < 100000:
                            extracted[tid] = str(val)
                            break
                break
    return extracted


# ── existing routes ────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    labs = load_labs()
    return render_template("index.html", labs=labs, current_year=datetime.now().year)

@app.route("/panel", methods=["POST"])
def panel():
    lab_id = request.form.get("lab_id")
    if not lab_id:
        return redirect(url_for("index"))
    lab = find_lab(lab_id)
    if not lab:
        return render_template("index.html", labs=load_labs(),
                               error=f"No panel found for '{lab_id}'",
                               current_year=datetime.now().year)
    return render_template("result.html", lab=lab, interpretations=None, summary=None,
                           age=request.form.get("age", ""), gender=request.form.get("gender", ""),
                           current_year=datetime.now().year)

@app.route("/interpret", methods=["POST"])
def interpret():
    lab_id = request.form.get("lab_id")
    lab = find_lab(lab_id)
    if not lab:
        return render_template("index.html", labs=load_labs(),
                               error="Selected panel not found",
                               current_year=datetime.now().year)
    age = request.form.get("age", "")
    gender = request.form.get("gender", "").lower()
    interpretations = []
    abnormal_any = False
    for test in lab.get("tests", []):
        tid = test.get("id")
        name = test.get("name")
        units = test.get("units", "")
        raw = request.form.get(tid, "").strip()
        value = None
        if raw != "":
            try:
                value = float(raw)
                if not math.isfinite(value):
                    value = None
            except Exception:
                value = None
        normal_str = test.get("normal_range", "")
        low, high = None, None
        try:
            s = normal_str
            if ("Male:" in s or "Female:" in s) and gender in ("male", "female"):
                parts = [p.strip() for p in s.split(",")]
                for p in parts:
                    if gender == "male" and p.lower().startswith("male"):
                        rng = p.split(":", 1)[1].strip()
                        low, high = [float(x.strip()) for x in rng.replace("–", "-").split("-")[:2]]
                        break
                    if gender == "female" and p.lower().startswith("female"):
                        rng = p.split(":", 1)[1].strip()
                        low, high = [float(x.strip()) for x in rng.replace("–", "-").split("-")[:2]]
                        break
            if low is None:
                rng = s.replace("–", "-")
                if "-" in rng:
                    nums = [x.strip() for x in rng.split("-")]
                    low = float("".join(ch for ch in nums[0] if ch.isdigit() or ch == ".").strip())
                    high = float("".join(ch for ch in nums[1] if ch.isdigit() or ch == ".").strip())
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
                    detail = test.get("low_causes", "")
                    abnormal_any = True
                elif value > high:
                    status = "High"
                    detail = test.get("high_causes", "")
                    abnormal_any = True
                else:
                    status = "Normal"
            else:
                status = "Value entered"
        interpretations.append({
            "id": tid, "name": name, "value": value,
            "units": units, "normal_range": normal_str,
            "status": status, "detail": detail
        })
    summary = generate_summary(lab["id"].lower(), interpretations, age, gender)
    return render_template("result.html", lab=lab, interpretations=interpretations,
                           summary=summary, age=age, gender=gender,
                           current_year=datetime.now().year)


def generate_summary(lab_id, interpretations, age, gender):
    abn = {i["name"]: i for i in interpretations if i["status"] in ("Low", "High")}
    if lab_id == "cbc":
        hb  = abn.get("Hemoglobin") or next((i for i in interpretations if i["name"] == "Hemoglobin"), None)
        mcv = abn.get("Mean Corpuscular Volume (MCV)") or next((i for i in interpretations if i["name"].startswith("Mean Corpuscular Volume")), None)
        wbc = abn.get("White Blood Cells") or next((i for i in interpretations if i["name"].startswith("White")), None)
        plt = abn.get("Platelet Count") or next((i for i in interpretations if "Platelet" in i["name"]), None)
        parts = []
        if hb and hb["value"] is not None and hb["status"] == "Low":
            if mcv and mcv["value"] is not None:
                if mcv["value"] < 80:
                    parts.append("Microcytic anemia — consider iron deficiency or thalassemia.")
                elif mcv["value"] > 100:
                    parts.append("Macrocytic anemia — consider B12/folate deficiency.")
                else:
                    parts.append("Normocytic anemia — consider acute blood loss or chronic disease.")
            else:
                parts.append("Anemia (low hemoglobin) — correlate with MCV for classification.")
        if wbc and wbc["value"] is not None and wbc["status"] == "High":
            parts.append("Leukocytosis — possible infection or inflammation.")
        if plt and plt["value"] is not None and plt["status"] == "Low":
            parts.append("Thrombocytopenia — consider viral infection, DIC, or marrow suppression.")
        return " ".join(parts) if parts else "CBC: no major abnormalities detected."
    if lab_id == "rft":
        creat = next((i for i in interpretations if i["name"] == "Serum Creatinine" or i["id"] == "creatinine"), None)
        urea  = next((i for i in interpretations if "Urea" in i["name"] or i["id"] == "urea"), None)
        na    = next((i for i in interpretations if "Sodium" in i["name"] or i["id"] == "na"), None)
        k     = next((i for i in interpretations if "Potassium" in i["name"] or i["id"] == "k"), None)
        parts = []
        if creat and creat["value"] is not None and creat["status"] == "High":
            parts.append("Raised creatinine — suggests renal impairment or AKI; correlate clinically.")
        if urea and urea["value"] is not None and urea["status"] == "High":
            parts.append("High urea — consider dehydration, renal impairment or high protein intake.")
        if na and na["value"] is not None and na["status"] != "Normal":
            parts.append("Sodium abnormality — check volume status and medications.")
        if k and k["value"] is not None and k["status"] != "Normal":
            parts.append("Potassium abnormality — beware of arrhythmia risk if severe.")
        return " ".join(parts) if parts else "RFT: no major abnormalities detected."
    if lab_id == "lft":
        alt = next((i for i in interpretations if "ALT" in i["name"] or i["id"] == "alt"), None)
        alb = next((i for i in interpretations if "Albumin" in i["name"] or i["id"] == "alb"), None)
        parts = []
        if alt and alt["value"] is not None and alt["status"] == "High":
            parts.append("Raised ALT/AST — suggests hepatocellular injury (e.g., hepatitis, drug toxicity).")
        if alb and alb["value"] is not None and alb["status"] == "Low":
            parts.append("Low albumin — consider chronic liver disease or protein loss.")
        return " ".join(parts) if parts else "LFT: no major abnormalities detected."
    if lab_id == "tft":
        tsh = next((i for i in interpretations if i["name"] == "TSH" or i["id"] == "tsh"), None)
        if tsh and tsh["value"] is not None:
            if tsh["status"] == "High":
                return "Likely hypothyroidism (high TSH). Correlate with free T4."
            if tsh["status"] == "Low":
                return "Likely hyperthyroidism (suppressed TSH). Correlate with free T4/T3."
        return "TFT: no major abnormalities detected."
    if lab_id == "abg":
        ph   = next((i for i in interpretations if i["id"] == "ph"), None)
        pco2 = next((i for i in interpretations if i["id"] == "pco2"), None)
        hco3 = next((i for i in interpretations if i["id"] == "hco3"), None)
        if not ph or not pco2 or not hco3 or None in (ph["value"], pco2["value"], hco3["value"]):
            return "ABG: incomplete data — please enter values for pH, pCO₂, and HCO₃⁻."
        ph_val, pco2_val, hco3_val = ph["value"], pco2["value"], hco3["value"]
        if ph_val < 7.35:
            if pco2_val > 45 and hco3_val >= 22:
                return "Respiratory Acidosis — likely due to hypoventilation (e.g., COPD, CNS depression)."
            if hco3_val < 22 and pco2_val <= 45:
                return "Metabolic Acidosis — consider DKA, renal failure, or lactic acidosis."
            if pco2_val > 45 and hco3_val < 22:
                return "Mixed Acidosis — both respiratory and metabolic components present."
            return "Acidemia — unclassified pattern; check for mixed disorder."
        if ph_val > 7.45:
            if pco2_val < 35 and hco3_val <= 26:
                return "Respiratory Alkalosis — likely due to hyperventilation (anxiety, hypoxia, sepsis)."
            if hco3_val > 26 and pco2_val >= 35:
                return "Metabolic Alkalosis — may result from vomiting, diuretics, or bicarbonate excess."
            if pco2_val < 35 and hco3_val > 26:
                return "Mixed Alkalosis — combined respiratory and metabolic causes."
            return "Alkalemia — unclear pattern, consider mixed disorder."
        return "Normal ABG — no major acid-base disturbance detected."
    return "No focused summary available for this panel."

if __name__ == "__main__":
    app.run(debug=True)
