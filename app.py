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
                "isOverlayRequired": True,
                "detectOrientation": True,
                "scale": True,
                "OCREngine": 1,
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

    # Try coordinate-based extraction first (handles multi-column tables)
    extracted = extract_by_coordinates(parsed[0], lab)

    # Fallback to text-based if coordinates got nothing
    if not extracted:
        full_text = parsed[0].get("ParsedText", "")
        extracted = extract_values(full_text, lab)

    full_text = parsed[0].get("ParsedText", "")
    return jsonify({"extracted": extracted, "raw_text": full_text})


def extract_by_coordinates(parsed_result, lab):
    extracted = {}
    try:
        lines_data = parsed_result.get("TextOverlay", {}).get("Lines", [])
        if not lines_data:
            return extracted

        all_words = []
        for line in lines_data:
            for w in line.get("Words", []):
                all_words.append({
                    "text": w["WordText"],
                    "left": w["Left"],
                    "top":  w["Top"],
                    "width": w["Width"],
                })

        if not all_words:
            return extracted

        max_right = max(w["left"] + w["width"] for w in all_words)
        val_col_min = max_right * 0.18
        val_col_max = max_right * 0.52

        aliases = {}
        for test in lab["tests"]:
            tid = test["id"]
            for kw in EXTRA_ALIASES.get(tid, []):
                aliases[normalize(kw)] = tid
            aliases[normalize(test["name"])] = tid
            aliases[normalize(tid)] = tid
            wlist = test["name"].split()
            if len(wlist) > 1:
                aliases[normalize("".join(w[0] for w in wlist))] = tid

        lines_data2 = parsed_result.get("TextOverlay", {}).get("Lines", [])
        for line in lines_data2:
            words = line.get("Words", [])
            if not words:
                continue
            line_text = " ".join(w["WordText"] for w in words)
            line_norm = normalize(line_text)

            matched_tid = None
            for alias in sorted(aliases.keys(), key=len, reverse=True):
                if alias in line_norm:
                    matched_tid = aliases[alias]
                    break
            if not matched_tid or matched_tid in extracted:
                continue

            for w in words:
                left = w["Left"]
                if val_col_min <= left <= val_col_max:
                    txt = w["WordText"].strip().replace(",", ".")
                    nums = re.findall(r"^\d+\.?\d*$", txt)
                    if nums:
                        val = float(nums[0])
                        if 0 < val < 100000:
                            extracted[matched_tid] = str(val)
                            break
    except Exception:
        pass
    return extracted


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
        hb  = next((i for i in interpretations if i["id"] == "hb"), None)
        mcv = next((i for i in interpretations if i["id"] == "mcv"), None)
        wbc = next((i for i in interpretations if i["id"] == "wbc"), None)
        plt = next((i for i in interpretations if i["id"] == "plt"), None)
        parts = []
        if hb and hb["value"] is not None and hb["status"] == "Low":
            if mcv and mcv["value"] is not None:
                if mcv["value"] < 80:
                    parts.append("Your hemoglobin is low and your red blood cells are smaller than normal. This pattern is most commonly caused by iron deficiency. Low iron is very common and very treatable — please see a doctor who may recommend iron supplements or further tests.")
                elif mcv["value"] > 100:
                    parts.append("Your hemoglobin is low and your red blood cells are larger than normal. This is often caused by low Vitamin B12 or folate. Please see a doctor — both are easily treated with supplements.")
                else:
                    parts.append("Your hemoglobin is low with normal sized red blood cells. This type of anemia can be caused by a chronic illness, recent blood loss, or kidney problems. Please see a doctor to find out the cause.")
            else:
                parts.append("Your hemoglobin is low which means you may have anemia. You might feel tired, weak, or short of breath. Please see a doctor to find out the cause and get the right treatment.")
        if wbc and wbc["value"] is not None and wbc["status"] == "High":
            parts.append("Your white blood cell count is high which usually means your body is fighting an infection. This is common with bacterial infections. Please see a doctor if you have fever, pain, or feel generally unwell.")
        if plt and plt["value"] is not None and plt["status"] == "Low":
            parts.append("Your platelet count is low which means your blood may take longer to clot. You may notice easy bruising. Please see a doctor promptly.")
        if not parts:
            return "Your CBC results look largely normal. No major abnormalities were detected. If you have any symptoms or concerns, please discuss with your doctor."
        parts.append("Remember — this is a general guide only. Please consult your doctor for a proper diagnosis.")
        return " ".join(parts)

    if lab_id == "rft":
        creat = next((i for i in interpretations if i["id"] == "creatinine"), None)
        urea  = next((i for i in interpretations if i["id"] == "urea"), None)
        na    = next((i for i in interpretations if i["id"] == "na"), None)
        k     = next((i for i in interpretations if i["id"] == "k"), None)
        parts = []
        if creat and creat["value"] is not None and creat["status"] == "High":
            parts.append("Your creatinine is high which is an important sign that your kidneys may not be filtering waste properly. Please see a doctor as soon as possible for further evaluation.")
        if urea and urea["value"] is not None and urea["status"] == "High":
            parts.append("Your urea is high. This can be caused by dehydration, kidney problems, or a high protein diet. Please make sure you are drinking enough water and see a doctor.")
        if na and na["value"] is not None and na["status"] != "Normal" and na["status"] != "Not entered":
            parts.append("Your sodium level is outside the normal range. Sodium is important for fluid balance in your body. Please see a doctor.")
        if k and k["value"] is not None and k["status"] != "Normal" and k["status"] != "Not entered":
            parts.append("Your potassium level is outside the normal range. This can affect your heart and muscles. Please see a doctor promptly.")
        if not parts:
            return "Your kidney function tests look largely normal. No major abnormalities detected. If you have any symptoms or concerns, please speak to your doctor."
        parts.append("Please consult your doctor for a proper assessment.")
        return " ".join(parts)

    if lab_id == "lft":
        alt = next((i for i in interpretations if i["id"] == "alt"), None)
        alb = next((i for i in interpretations if i["id"] == "alb"), None)
        tbil = next((i for i in interpretations if i["id"] == "tbil"), None)
        parts = []
        if alt and alt["value"] is not None and alt["status"] == "High":
            parts.append("Your liver enzyme (ALT) is high which means your liver may be under stress. Common causes include fatty liver, hepatitis, or certain medications. Please see a doctor for further evaluation.")
        if alb and alb["value"] is not None and alb["status"] == "Low":
            parts.append("Your albumin is low. This protein is made by the liver and a low level can be a sign of liver disease or poor nutrition. Please see a doctor.")
        if tbil and tbil["value"] is not None and tbil["status"] ==="High":
            parts.append("Your bilirubin is high. If your skin or eyes look yellow, please see a doctor urgently. Otherwise please arrange an appointment soon.")
        if not parts:
            return "Your liver function tests look largely normal. No major abnormalities detected. If you have any symptoms or concerns, please speak to your doctor."
        parts.append("Please consult your doctor for a proper assessment.")
        return " ".join(parts)

    if lab_id == "tft":
        tsh = next((i for i in interpretations if i["id"] == "tsh"), None)
        if tsh and tsh["value"] is not None:
            if tsh["status"] == "High":
                return "Your TSH is high which suggests your thyroid gland may be underactive (hypothyroidism). Common symptoms include feeling tired, weight gain, feeling cold, and dry skin. The good news is this is very treatable. Please see a doctor."
            if tsh["status"] == "Low":
                return "Your TSH is low which suggests your thyroid gland may be overactive (hyperthyroidism). Common symptoms include feeling anxious, weight loss, fast heartbeat, and feeling hot. Please see a doctor for further tests."
        return "Your thyroid function tests look normal. No major abnormalities detected. If you have any symptoms, please speak to your doctor."

    if lab_id == "abg":
        ph   = next((i for i in interpretations if i["id"] == "ph"), None)
        pco2 = next((i for i in interpretations if i["id"] == "pco2"), None)
        hco3 = next((i for i in interpretations if i["id"] == "hco3"), None)
        if not ph or not pco2 or not hco3 or None in (ph["value"], pco2["value"], hco3["value"]):
            return "Please enter values for pH, pCO₂, and HCO₃⁻ to get a summary."
        ph_val, pco2_val, hco3_val = ph["value"], pco2["value"], hco3["value"]
        if ph_val < 7.35:
            if pco2_val > 45 and hco3_val >= 22:
                return "Your results suggest your blood is too acidic due to a breathing problem (respiratory acidosis). This means the lungs may not be removing enough carbon dioxide. This needs medical attention."
            if hco3_val < 22 and pco2_val <= 45:
                return "Your results suggest your blood is too acidic due to a metabolic cause (metabolic acidosis). This can happen with uncontrolled diabetes, kidney problems, or other conditions. Please seek medical attention."
            if pco2_val > 45 and hco3_val < 22:
                return "Your results show a mixed pattern where both breathing and metabolic factors are making your blood too acidic. This needs urgent medical attention."
            return "Your blood appears to be more acidic than normal. Please seek medical attention."
        if ph_val > 7.45:
            if pco2_val < 35 and hco3_val <= 26:
                return "Your results suggest your blood is too alkaline due to a breathing pattern (respiratory alkalosis). This can happen with anxiety or rapid breathing. Please see a doctor."
            if hco3_val > 26 and pco2_val >= 35:
                return "Your results suggest your blood is too alkaline due to a metabolic cause. This can happen with prolonged vomiting or certain medications. Please see a doctor."
            return "Your blood appears to be more alkaline than normal. Please see a doctor."
        return "Your blood gas values appear to be within a normal range. No major acid-base disturbance detected."

    if lab_id == "lipid":
        ldl   = next((i for i in interpretations if i["id"] == "ldl"), None)
        hdl   = next((i for i in interpretations if i["id"] == "hdl"), None)
        trig  = next((i for i in interpretations if i["id"] == "trig"), None)
        tchol = next((i for i in interpretations if i["id"] == "total_chol"), None)
        parts = []
        if ldl and ldl["value"] is not None and ldl["status"] == "High":
            parts.append("Your bad cholesterol (LDL) is high which increases your risk of heart disease and stroke. Diet changes and possibly medication can bring this down — please see a doctor.")
        if hdl and hdl["value"] is not None and hdl["status"] == "Low":
            parts.append("Your good cholesterol (HDL) is low. Regular exercise, quitting smoking, and a healthy diet can help raise it.")
        if trig and trig["value"] is not None and trig["status"] == "High":
            parts.append("Your triglycerides are high — this is often linked to a diet high in sugar and refined carbohydrates. Cutting down on sugary foods and drinks can make a big difference.")
        if tchol and tchol["value"] is not None and tchol["status"] == "High":
            parts.append("Your total cholesterol is above the recommended level.")
        if not parts:
            return "Your lipid profile looks largely normal. Keep maintaining a healthy diet and active lifestyle. Please discuss with your doctor if you have any heart related symptoms."
        parts.append("Please see a doctor to discuss your heart health and get personalised advice.")
        return " ".join(parts)

    if lab_id == "diabetes":
        fbs   = next((i for i in interpretations if i["id"] == "fbs"), None)
        hba1c = next((i for i in interpretations if i["id"] == "hba1c"), None)
        rbs   = next((i for i in interpretations if i["id"] == "rbs"), None)
        parts = []
        if hba1c and hba1c["value"] is not None:
            if hba1c["value"] >= 6.5:
                parts.append("Your HbA1c suggests diabetes. This means your blood sugar has been consistently high over the past 3 months. Please see a doctor as soon as possible — with proper management you can live a completely normal life.")
            elif hba1c["value"] >= 5.7:
                parts.append("Your HbA1c is in the prediabetes range. This is a warning sign that your blood sugar is higher than it should be. The good news is that lifestyle changes at this stage can prevent diabetes from developing.")
        if fbs and fbs["value"] is not None and fbs["status"] == "High":
            parts.append("Your fasting blood sugar is above normal. Please see a doctor for a proper diabetes assessment.")
        if rbs and rbs["value"] is not None and rbs["status"] == "High":
            parts.append("Your random blood sugar is high. Please see a doctor to check for diabetes.")
        if not parts:
            return "Your blood sugar results look normal. Keep maintaining a healthy diet, staying active, and maintaining a healthy weight to prevent diabetes. Well done!"
        parts.append("Remember — diabetes is very manageable. Early action makes a big difference.")
        return " ".join(parts)

    if lab_id == "iron":
        ferritin = next((i for i in interpretations if i["id"] == "ferritin"), None)
        s_iron   = next((i for i in interpretations if i["id"] == "s_iron"), None)
        tibc     = next((i for i in interpretations if i["id"] == "tibc"), None)
        parts = []
        if ferritin and ferritin["value"] is not None and ferritin["status"] == "Low":
            parts.append("Your ferritin is low which means your iron stores are depleted. This is the most common cause of fatigue especially in women. Iron supplements prescribed by a doctor can help significantly.")
        if s_iron and s_iron["value"] is not None and s_iron["status"] == "Low":
            parts.append("Your serum iron is low. Combined with your other results this points to iron deficiency. Please see a doctor for treatment.")
        if tibc and tibc["value"] is not None and tibc["status"] == "High":
            parts.append("Your TIBC is high which is consistent with iron deficiency — your body is trying to absorb more iron.")
        if not parts:
            return "Your iron studies look normal. No significant iron deficiency detected. If you still feel unusually tired please discuss other possible causes with your doctor."
        parts.append("Iron deficiency is very common and very treatable. Please see a doctor for the right supplements and dose.")
        return " ".join(parts)

    if lab_id == "vitamins":
        vit_d  = next((i for i in interpretations if i["id"] == "vit_d"), None)
        vit_b12 = next((i for i in interpretations if i["id"] == "vit_b12"), None)
        parts = []
        if vit_d and vit_d["value"] is not None and vit_d["status"] == "Low":
            parts.append("Your Vitamin D is low — this is extremely common in Pakistan. It can cause fatigue, bone pain, and low mood. A doctor can prescribe the right dose of Vitamin D supplements for you.")
        if vit_b12 and vit_b12["value"] is not None and vit_b12["status"] == "Low":
            parts.append("Your Vitamin B12 is low. B12 deficiency can cause tiredness, tingling in hands and feet, and memory issues. It is easily treated with supplements or injections. Please see a doctor.")
        if not parts:
            return "Your Vitamin D and B12 levels look normal. Keep taking supplements if prescribed and maintain a balanced diet."
        return " ".join(parts)

    if lab_id == "hepatitis":
        hbsag   = next((i for i in interpretations if i["id"] == "hbsag"), None)
        anti_hcv = next((i for i in interpretations if i["id"] == "anti_hcv"), None)
        parts = []
        if hbsag and hbsag["status"] == "High":
            parts.append("Your Hepatitis B test is positive. Please see a doctor or liver specialist as soon as possible. Hepatitis B is manageable with regular monitoring and treatment when needed.")
        if anti_hcv and anti_hcv["status"] == "High":
            parts.append("Your Hepatitis C test is positive. Please see a doctor immediately for a confirmatory PCR test. Hepatitis C is now completely curable with oral medicines available in Pakistan — do not delay.")
        if not parts:
            return "Your hepatitis markers look normal. No Hepatitis B or C infection detected. If you have not been vaccinated against Hepatitis B please speak to your doctor about getting vaccinated."
        return " ".join(parts)

    return "No summary available for this panel. Please consult your doctor for interpretation."


# ── SEO routes ─────────────────────────────────────────────────────────────────
@app.route("/sitemap.xml")
def sitemap():
    from flask import Response
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://labassist.online/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    from flask import Response
    txt = """User-agent: *
Allow: /
Sitemap: https://labassist.online/sitemap.xml"""
    return Response(txt, mimetype="text/plain")

if __name__ == "__main__":
    app.run(debug=True)
