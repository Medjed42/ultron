from pathlib import Path
import os
import json
import webbrowser
import subprocess
from tools.web_search_tool import deep_search
from tools.site_maker_tool import create_site
from tools.discord_logger import send_to_discord
from agents.omni_intelligence import OmniIntelligenceAgent
from agents.prompts import EXTRACTION_PROMPT_V2, VERIFICATION_PROMPT_V2, WEBSITE_BRIEF_V3, SALES_EMAIL_V2
from db_layer import get_lead, update_lead
from evidence import validate_claims, enrich_evidence
from config import config
import hashlib
import re

def run_restaurant_acquisition_workflow(target: str = "", source: str = "car"):
    send_to_discord(f"🚀 **Počinjemo Autonomous Agency Pipeline v2.0 (Izvor: {source.upper()})**", agent="car")
    
    # Switched to OmniIntelligence (Gemini -> Groq Fallback)
    ai_agent = OmniIntelligenceAgent()
    target_name = target.strip()
    
    # ---------------------------------------------------------
    # STATE 1: DISCOVERY & RESEARCH
    # ---------------------------------------------------------
    if not target_name:
        send_to_discord("🔎 **[DISCOVERY] Executing multi-query discovery for missing websites...**", agent="car")
        search_results = deep_search("restorani srbija bez sajta kontakt", max_results=15)
        
        analysis_prompt = f"Ovo su rezultati pretrage: {search_results}. Pronađi jedan restoran koji deluje da nema sajt. Vrati samo njegovo IME kao čist tekst."
        res = ai_agent.execute_task(analysis_prompt, [])
        target_name = res.get("result") if res.get("status") == "success" else None
        
        if not target_name:
            send_to_discord("⚠️ **[DISCOVERY] Failed to discover a target. Aborting.**", agent="car", level="ERROR")
            return {"status": "error", "message": "Discovery failed."}
            
    # Stable ID generation
    restaurant_id = re.sub(r"[^a-z0-9_-]", "_", target_name.lower())[:60] + "_" + hashlib.sha256(target_name.casefold().encode()).hexdigest()[:10]
    lead = get_lead(restaurant_id)
    
    if lead and lead.get("pipeline_state") == "COMPLETED":
        send_to_discord(f"⚠️ **[IDEMPOTENCY] Lead {target_name} is already COMPLETED. Skipping.**", agent="car", level="WARNING")
        return {"status": "success", "message": "Already processed."}
        
    update_lead(restaurant_id, {"canonical_name": target_name, "pipeline_state": "RESEARCHING"})
    send_to_discord(f"🎯 **[RESEARCH] Target Locked:** {target_name}. Beginning Deep Research...", agent="car")

    # ---------------------------------------------------------
    # STATE 2: EXTRACT
    # ---------------------------------------------------------
    send_to_discord(f"🔍 **[EXTRACT] Pulling multiple source streams for {target_name}...**", agent="chatgpt")
    deep_info_search = deep_search(f"{target_name} restoran srbija", max_results=10, use_cache=False)
    deep_info_search = enrich_evidence(deep_info_search)
    
    if deep_info_search.get("status") != "success":
        update_lead(restaurant_id, {"pipeline_state":"NEEDS_REVIEW", "reason":"No retrievable evidence"})
        return {"status":"needs_review", "message":"Search provided no reliable source records."}
    ext_prompt = EXTRACTION_PROMPT_V2.format(evidence=deep_info_search)
    res_ext = ai_agent.execute_task(ext_prompt, [], require_json=True)
    
    if res_ext.get("status") != "success":
        send_to_discord(f"⚠️ **[EXTRACT] Failed to extract data: {res_ext.get('message')}**", agent="chatgpt", level="ERROR")
        update_lead(restaurant_id, {"pipeline_state": "FAILED_EXTRACTION"})
        return res_ext
        
    try:
        extracted_data = json.loads(res_ext.get("result", "{}"))
    except:
        extracted_data = {}

    # ---------------------------------------------------------
    # STATE 3: VERIFY & NORMALIZE
    # ---------------------------------------------------------
    send_to_discord(f"🛡️ **[VERIFY] Executing Claim-Level Provenance & Conflict Resolution...**", agent="chatgpt")
    ver_prompt = VERIFICATION_PROMPT_V2.format(raw_evidence=deep_info_search, extracted_data=json.dumps(extracted_data))
    res_ver = ai_agent.execute_task(ver_prompt, [], require_json=True)
    
    try:
        verified_data = validate_claims(json.loads(res_ver.get("result", "{}")), deep_info_search)
        confidence = verified_data.get("overall_confidence", 0.0)
    except:
        verified_data = {"claims": [], "overall_confidence": 0.0}
        confidence = 0.0
        
    update_lead(restaurant_id, {"claims": verified_data.get("claims", []), "confidence": confidence, "pipeline_state": "VERIFIED" if confidence >= config.MIN_RESEARCH_CONFIDENCE else "NEEDS_REVIEW"})
    send_to_discord(f"✅ **[VERIFY] Complete. Confidence Score:** {confidence * 100}%\n{json.dumps(verified_data, indent=2)[:500]}...", agent="chatgpt")
    
    # ---------------------------------------------------------
    # STATE 4: DECIDE (Minimum Confidence Gate)
    # ---------------------------------------------------------
    send_to_discord(f"⚖️ **[DECIDE] Evaluating Action Safety Gates...**", agent="car")
    if confidence < config.MIN_RESEARCH_CONFIDENCE:
        send_to_discord(f"🛑 **[DECIDE] Confidence ({confidence}) too low. Aborting autonomous action.**", agent="car", level="WARNING")
        update_lead(restaurant_id, {"pipeline_state": "NEEDS_REVIEW"})
        return {"status": "needs_review", "message": "Evidence needs human review; no website handoff or outreach performed.", "claims": verified_data.get("claims", [])}

    # ---------------------------------------------------------
    # STATE 5: ACT (Website Generation)
    # ---------------------------------------------------------
    send_to_discord(f"✨ **[ACT] Generating Grounded Website Brief...**", agent="chatgpt")
    brief_prompt = WEBSITE_BRIEF_V3.format(verified_facts=json.dumps(verified_data))
    res_brief = ai_agent.execute_task(brief_prompt, [])
    if res_brief.get("status") != "success":
        update_lead(restaurant_id, {"pipeline_state":"FAILED_BRIEF"})
        return res_brief
    mega_prompt = res_brief["result"]
    
    send_to_discord(f"🏗️ **[ACT] Handoff to Autonomous Engineer (Antigravity)...**", agent="antigravity")
    site_result = create_site(restaurant_id, mega_prompt)
    
    site_url = ""
    local_path = ""
    if site_result["status"] == "success":
        local_path = os.path.realpath(site_result['path'])
        index_file = os.path.join(local_path, "index.html")
        site_url = f"file:///{index_file.replace(chr(92), '/')}"
        send_to_discord(f"✅ **[ACT] Site generated securely!** Path: {local_path}", agent="antigravity")
        update_lead(restaurant_id, {"pipeline_state": "DEMO_CREATED", "demo_url": site_url})
    else:
        send_to_discord(f"❌ **[ACT] Antigravity Build Failed:** {site_result.get('message')}", agent="antigravity", level="ERROR")
        update_lead(restaurant_id, {"pipeline_state": "FAILED_BUILD"})
        return site_result
        
    # ---------------------------------------------------------
    # STATE 6: ACT (Email Generation)
    # ---------------------------------------------------------
    send_to_discord(f"✍️ **[ACT] Drafting Personalized B2B Outreach...**", agent="chatgpt")
    email_prompt = SALES_EMAIL_V2.format(verified_facts=json.dumps(verified_data))
    res_email = ai_agent.execute_task(email_prompt, [])
    if res_email.get("status") != "success":
        update_lead(restaurant_id, {"pipeline_state":"FAILED_DRAFT"})
        return res_email
    email_draft = res_email["result"]
    update_lead(restaurant_id, {"email_draft":email_draft, "website_brief":mega_prompt})
    
    send_to_discord(f"📧 **[ACT] Draft ready for review:**\n\n{email_draft[:500]}...", agent="chatgpt")
    update_lead(restaurant_id, {"pipeline_state": "COMPLETED"})
    
    # ---------------------------------------------------------
    # NOTIFICATION LIAISON
    # ---------------------------------------------------------
    send_to_discord(f"**ULTRON AI ASSISTENT:**\n🎯 [FINISH] Agency Pipeline Successfully Completed!", agent="car")
    
    if site_result["status"] == "success":
        try:
            import requests
            tg_token = config.TELEGRAM_BOT_TOKEN
            chat_id_file = str(Path(__file__).resolve().parents[3] / "telegram_temp" / "chat_id.txt")
            if os.path.exists(chat_id_file):
                with open(chat_id_file, "r") as f:
                    chat_id = f.read().strip()
                requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"✅ Lead {target_name} processed.\nConfidence: {confidence}\nDemo: {site_url}"
                }, timeout=10)
        except Exception as e:
            pass

        if source.lower() == "car":
            try:
                webbrowser.open(site_url)
                msg = f"Gospodine, proces je završen. Agencija je prikupila podatke sa pouzdanošću od {int(confidence*100)} posto i izgenerisala demo."
                cmd = f'edge-tts --voice hr-HR-SreckoNeural --text "{msg}" --write-media omni_done.mp3 && ffplay -nodisp -autoexit omni_done.mp3'
                subprocess.Popen(cmd, shell=True)
            except Exception as e:
                pass
                
    return {"status": "success", "site_url": site_url}
