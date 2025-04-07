import streamlit as st
import os
import json
import datetime
import time
import requests
import base64
from pydub import AudioSegment
import shutil
import traceback
from io import BytesIO
import tempfile
import threading
import re

# הגדרת הכותרת וסגנון האפליקציה
st.set_page_config(
    page_title="מערכת תמלול שיעורי תורה",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# כותרת ראשית
st.title("מערכת תמלול שיעורי תורה")
st.markdown("### המערכת מאפשרת תמלול אוטומטי של שיעורים עם Google Gemini")

# סידור כיוון הטקסט לעברית
st.markdown("""
<style>
    body {
        direction: rtl;
        text-align: right;
    }
    .stTextInput, .stTextArea {
        direction: rtl;
        text-align: right;
    }
    .streamlit-expanderHeader {
        direction: rtl;
        text-align: right;
    }
    p, h1, h2, h3, h4, h5, h6, div {
        direction: rtl;
        text-align: right;
    }
    .stButton button {
        float: right;
    }
</style>
""", unsafe_allow_html=True)

class TokenUsageManager:
    """מנהל שימוש בטוקנים בפרויקטים שונים של Google AI Studio."""
    
    def __init__(self, usage_file="token_usage.json"):
        self.usage_file = usage_file
        self.usage_data = self._load_usage_data()
        
    def _load_usage_data(self):
        """טעינת נתוני שימוש בטוקנים מקובץ, או יצירת קובץ חדש אם לא קיים."""
        if os.path.exists(self.usage_file):
            try:
                with open(self.usage_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                st.warning(f"אזהרה: נכשל בטעינת נתוני שימוש בטוקנים: {e}")
        
        # מבנה ברירת מחדל אם הקובץ לא קיים או לא תקין
        return {
            "projects": {},
            "last_updated": datetime.datetime.now().strftime("%Y-%m-%d")
        }
    
    def _save_usage_data(self):
        """שמירת נתוני שימוש בטוקנים לקובץ."""
        try:
            with open(self.usage_file, "w") as f:
                json.dump(self.usage_data, f, indent=2)
        except Exception as e:
            st.warning(f"אזהרה: נכשל בשמירת נתוני שימוש בטוקנים: {e}")
    
    def reset_daily_counters_if_needed(self):
        """איפוס מונה יומי אם התאריך התחלף."""
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        last_updated = self.usage_data.get("last_updated", "")
        
        if today != last_updated:
            st.info(f"זוהה יום חדש. מאפס את מוני השימוש היומי בטוקנים.")
            # איפוס שימוש יומי לכל הפרויקטים
            for project_id in self.usage_data["projects"]:
                if "daily_usage" in self.usage_data["projects"][project_id]:
                    self.usage_data["projects"][project_id]["daily_usage"] = 0
            
            self.usage_data["last_updated"] = today
            self._save_usage_data()
    
    def register_project(self, project_id, daily_limit=1000000):
        """רישום פרויקט חדש או עדכון המגבלות שלו."""
        if project_id not in self.usage_data["projects"]:
            self.usage_data["projects"][project_id] = {
                "daily_limit": daily_limit,
                "daily_usage": 0,
                "total_usage": 0
            }
        else:
            # עדכון מגבלה יומית אם השתנתה
            self.usage_data["projects"][project_id]["daily_limit"] = daily_limit
        
        self._save_usage_data()
    
    def record_usage(self, project_id, tokens_used):
        """רישום שימוש בטוקנים לפרויקט."""
        if project_id not in self.usage_data["projects"]:
            self.register_project(project_id)
        
        self.usage_data["projects"][project_id]["daily_usage"] += tokens_used
        self.usage_data["projects"][project_id]["total_usage"] += tokens_used
        
        self._save_usage_data()
    
    def get_available_project(self, project_ids):
        """מציאת פרויקט עם טוקנים זמינים מרשימה נתונה."""
        self.reset_daily_counters_if_needed()
        
        for project_id in project_ids:
            if project_id not in self.usage_data["projects"]:
                # פרויקט חדש, רישום אוטומטי
                self.register_project(project_id)
                return project_id
            
            project_data = self.usage_data["projects"][project_id]
            if project_data["daily_usage"] < project_data["daily_limit"]:
                return project_id
        
        return None
    
    def get_usage_summary(self):
        """הצגת סיכום שימוש בטוקנים בכל הפרויקטים."""
        self.reset_daily_counters_if_needed()
        
        summary = []
        for project_id, data in self.usage_data["projects"].items():
            remaining = data["daily_limit"] - data["daily_usage"]
            percent_used = (data["daily_usage"] / data["daily_limit"]) * 100 if data["daily_limit"] > 0 else 0
            
            summary.append({
                "project_id": project_id,
                "daily_limit": data["daily_limit"],
                "daily_usage": data["daily_usage"],
                "remaining": remaining,
                "percent_used": percent_used,
                "total_usage": data["total_usage"]
            })
        
        return summary


def transcribe_with_gemini(api_key, model, prompt, audio_bytes, progress_bar=None):
    """תמלול אודיו באמצעות ה-API של Gemini"""
    
    # נקודת קצה של ה-API
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    # הכנת בקשה מרובת חלקים - זהו המבנה הנדרש לשליחת קבצים ל-Gemini
    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "audio/mp3",
                            "data": audio_b64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,  # טמפרטורה נמוכה לתמלול מדויק יותר
            "maxOutputTokens": 8192
        }
    }
    
    # ביצוע בקשה עם לוגיקת ניסיון חוזר
    max_retries = 3
    for retry in range(max_retries):
        try:
            response = requests.post(url, json=payload)
            
            if response.status_code == 200:
                break
            else:
                if progress_bar:
                    progress_bar.text(f"שגיאת API (ניסיון {retry+1}/{max_retries}): {response.status_code}")
                if retry < max_retries - 1:
                    time.sleep(2 * (retry + 1))  # המתנה גדלה אקספוננציאלית
        except Exception as e:
            if progress_bar:
                progress_bar.text(f"שגיאת בקשה (ניסיון {retry+1}/{max_retries}): {e}")
            if retry < max_retries - 1:
                time.sleep(2 * (retry + 1))
    
    if response.status_code != 200:
        raise Exception(f"בקשת Gemini נכשלה עם קוד {response.status_code}: {response.text}")
    
    # פענוח התשובה
    response_data = response.json()
    
    # חילוץ התמלול מהתשובה
    try:
        transcript = response_data["candidates"][0]["content"]["parts"][0]["text"]
        return transcript.strip()
    except (KeyError, IndexError) as e:
        if progress_bar:
            progress_bar.text(f"שגיאה בפענוח תשובת Gemini: {e}")
            progress_bar.text(f"מבנה התשובה: {json.dumps(response_data, indent=2)}")
        return ""


def process_audio(uploaded_file, api_key, projects, model, segment_length, overlap, custom_prompt, progress_bar, status_text):
    """עיבוד קובץ אודיו: טעינה, חלוקה, תמלול ושילוב"""
    
    # יצירת מנהל שימוש בטוקנים
    token_manager = TokenUsageManager()
    
    # רישום כל הפרויקטים
    project_ids = [p.strip() for p in projects.split(",") if p.strip()]
    if not project_ids:
        status_text.error("שגיאה: יש לספק לפחות מזהה פרויקט אחד של Google Cloud")
        return None
    
    for project_id in project_ids:
        token_manager.register_project(project_id)
    
    # הצגת שימוש בטוקנים לפני התחלה
    status_text.info("שימוש נוכחי בטוקנים בפרויקטים:")
    for project in token_manager.get_usage_summary():
        status_text.info(f"  {project['project_id']}: {project['daily_usage']}/{project['daily_limit']} טוקנים בשימוש ({project['percent_used']:.1f}%)")
    
    # יצירת ספריה זמנית
    with tempfile.TemporaryDirectory() as temp_dir:
        status_text.info(f"נוצרה ספריה זמנית: {temp_dir}")
        
        try:
            # שמירת הקובץ המועלה לדיסק
            mp3_path = os.path.join(temp_dir, "uploaded_audio.mp3")
            with open(mp3_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            status_text.info(f"מעבד קובץ אודיו: {uploaded_file.name}")
            
            # טעינת קובץ האודיו
            try:
                status_text.info("טוען קובץ אודיו...")
                audio = AudioSegment.from_mp3(mp3_path)
                status_text.info(f"קובץ אודיו נטען: {len(audio) / 1000 / 60:.2f} דקות")
            except Exception as e:
                status_text.error(f"נכשל בטעינת קובץ האודיו: {e}. ודא שהתקנת ffmpeg ושהוא נמצא ב-PATH שלך.")
                return None
            
            # חישוב גודל מקטע וחפיפה במילישניות
            segment_length_ms = segment_length * 60 * 1000
            overlap_ms = overlap * 1000
            
            if segment_length_ms <= overlap_ms:
                status_text.error(f"אורך המקטע ({segment_length} דקות) חייב להיות גדול מהחפיפה ({overlap} שניות)")
                return None
            
            # חישוב מספר המקטעים
            total_duration_ms = len(audio)
            effective_length_ms = segment_length_ms - overlap_ms
            num_segments = (total_duration_ms - overlap_ms + effective_length_ms - 1) // effective_length_ms
            
            status_text.info(f"האודיו יחולק ל-{num_segments} מקטעים:")
            status_text.info(f"- כל מקטע: מקסימום {segment_length} דקות")
            status_text.info(f"- חפיפה בין מקטעים: {overlap} שניות")
            
            # הגדרת מד התקדמות
            progress_bar.progress(0, text="מתחיל עיבוד...")
            
            # יצירת מקטעים
            segments = []
            for i in range(num_segments):
                start_ms = i * effective_length_ms
                end_ms = min(total_duration_ms, start_ms + segment_length_ms)
                
                # יצוא מקטע לקובץ זמני
                temp_file = os.path.join(temp_dir, f"segment_{i:03d}.mp3")
                status_text.info(f"יוצר מקטע {i+1}/{num_segments}: {start_ms/1000/60:.2f}-{end_ms/1000/60:.2f} דקות")
                
                # חילוץ מקטע ויצוא
                segment = audio[start_ms:end_ms]
                segment.export(temp_file, format="mp3")
                segments.append(temp_file)
                
                # עדכון מד התקדמות - שלב החלוקה למקטעים
                segment_progress = (i + 1) / (num_segments * 3)  # שליש ראשון של התהליך
                progress_bar.progress(segment_progress, text=f"חולק מקטע {i+1}/{num_segments}...")
            
            # עיבוד כל מקטע
            processed_transcriptions = process_segments(
                api_key, model, token_manager, project_ids, 
                segments, temp_dir, custom_prompt, 
                progress_bar, status_text, num_segments
            )
            
            if not processed_transcriptions:
                status_text.error("לא הצלחנו לקבל תמלול לאף מקטע")
                return None
            
            # שילוב כל התמלולים המעובדים
            combined_text = combine_transcriptions(processed_transcriptions, progress_bar, status_text)
            
            # הצג שימוש בטוקנים מעודכן
            status_text.info("\nשימוש בטוקנים מעודכן לאחר עיבוד:")
            for project in token_manager.get_usage_summary():
                status_text.info(f"  {project['project_id']}: {project['daily_usage']}/{project['daily_limit']} טוקנים בשימוש ({project['percent_used']:.1f}%)")
            
            progress_bar.progress(1.0, text="הושלם בהצלחה!")
            status_text.success("תמלול הקובץ הושלם בהצלחה")
            
            return combined_text
            
        except Exception as e:
            status_text.error(f"שגיאה: {str(e)}")
            traceback.print_exc()
            progress_bar.progress(1.0, text="נכשל")
            return None


def process_segments(api_key, model, token_manager, project_ids, segments, temp_dir, 
                    custom_prompt, progress_bar, status_text, num_segments):
    """עיבוד כל מקטע אודיו באמצעות תמלול ועיבוד LLM."""
    
    # פרומפט תמלול מותאם אישית או פרומפט ברירת מחדל
    if not custom_prompt or custom_prompt.strip() == "":
        # פרומפט תמלול ברירת מחדל
        base_transcription_prompt = """# תפקידך הוא תמלול מקצועי של שיעורי תורה עם דגש על דיוק בפרטים
## מטרה
אתה מתמלל מקצועי המתמחה בתמלול שיעורי תורה בעברית. עליך לייצר טקסט מדויק במיוחד תוך שימוש בהקשר להבנה נכונה של מילים, שמות ומונחים.

## הנחיות לדיוק מבוסס הקשר
### שמות ומושגים
- הקדש תשומת לב מיוחדת לשמות של רבנים, פרשנים, ספרים, וחכמי תורה
- השתמש בהקשר השיעור כדי לזהות נכון שמות ומונחים שנשמעים לא ברורים
- כשאתה נתקל במילים לא ברורות, התייחס להקשר המשפט, נושא השיחה, והטרמינולוגיה המתאימה
- היה זהיר במיוחד עם מילים הומופוניות בעברית (מילים שנשמעות דומה) ובחר את המשמעות הנכונה על פי ההקשר
- עבור מונחים מקצועיים (מונחי הלכה, מושגי ישיבה וכו'), השתמש בידע שלך כדי לזהות אותם במדויק
- אם הדיון עוסק בפרשנות או טקסט מסוים, ודא שהמונחים הקשורים מתומללים בצורה מדויקת
- שים לב להבחנות בין עברית לארמית בציטוטים

### דיוק בציטוטים מהמקורות
- היה מדויק במיוחד בציטוטי פסוקים מהתנ"ך
- הקפד על דיוק בציטוטים מחז"ל, גמרא, משנה והלכה
- סמן ציטוטים במירכאות ותקן שגיאות קלות בציטוט אם ישנן
- התייחס להקשר התוכן כדי להבין נכון ציטוטים חלקיים או מרומזים

### הבחנה בין דוברים
- הבחן בבירור בין דברי הרב לשאלות הקהל
- סמן שאלות מהקהל בפורמט: [שאלה מהקהל]: תוכן השאלה
- סמן את תשובת הרב בפורמט: [הרב]: תוכן התשובה
- אם יש דו-שיח ארוך, המשך לסמן כל חילופי דברים

### תיקונים חכמים
- הסר חזרות מיותרות ומילות מילוי אך שמור על דיוק בתוכן
- תקן שגיאות דיבור רק כאשר ברור מההקשר מה הכוונה האמיתית
- שמור על המשמעות המדויקת גם כשאתה מנסח מחדש משפטים לא ברורים
- בהתלבטות בין שתי אפשרויות פירוש, בחר את זו המתאימה יותר להקשר התוכן

## מה לא לעשות
- אל תנחש שמות או מונחים כשאתה לא בטוח, במקום זאת השתמש בהקשר להבנה טובה יותר
- אל תוסיף פרשנות או הסברים משלך
- אל תשנה את סגנון הדיבור של הרב
- אל תשמיט דוגמאות, מונחים מקצועיים או ציטוטים מורכבים גם אם הם קשים להבנה

## פורמט הפלט הסופי
הגש את התמלול כטקסט רציף עם חלוקה טבעית לפסקאות. השתמש ברווחים בין נושאים שונים ושמור על סדר הגיוני של הרעיונות.

נא לתמלל את ההקלטה המצורפת באופן מדויק לפי ההנחיות לעיל."""
    else:
        # שימוש בפרומפט מותאם אישית
        base_transcription_prompt = custom_prompt
    
    processed_transcriptions = []
    
    for i, segment_file in enumerate(segments):
        status_text.info(f"\nמעבד מקטע {i+1}/{len(segments)}")
        
        # שלב 1: תמלול ישירות עם Gemini
        raw_file = os.path.join(temp_dir, f"raw_{i:03d}.txt")
        
        # בדיקה אם תמלול גולמי כבר קיים (להמשך עיבוד שהופסק)
        if os.path.exists(raw_file):
            with open(raw_file, "r", encoding="utf-8") as f:
                raw_text = f.read()
            status_text.info(f"  משתמש בתמלול גולמי קיים: {len(raw_text)} תווים")
        else:
            # מציאת פרויקט זמין לתמלול
            project_id = token_manager.get_available_project(project_ids)
            if not project_id:
                status_text.error("לא נמצאו פרויקטים עם מכסת טוקנים זמינה. נסה שוב מחר.")
                return None
            
            try:
                status_text.info(f"  משתמש בפרויקט {project_id} לתמלול")
                
                # טעינת קובץ אודיו
                with open(segment_file, "rb") as audio_file:
                    audio_content = audio_file.read()
                
                # בדיקת גודל קובץ
                file_size = len(audio_content)
                status_text.info(f"  גודל קובץ אודיו: {file_size / 1024 / 1024:.2f} MB")
                
                # התאמת הפרומפט למקטע ספציפי
                segment_prompt = base_transcription_prompt
                if i == 0:
                    segment_prompt += "\n\nזהו החלק הראשון של ההקלטה."
                elif i == len(segments) - 1:
                    segment_prompt += f"\n\nזהו החלק האחרון של ההקלטה (חלק {i+1} מתוך {len(segments)})."
                else:
                    segment_prompt += f"\n\nזהו חלק אמצעי של ההקלטה (חלק {i+1} מתוך {len(segments)})."
                
                # עדכון מד התקדמות
                segment_progress_base = 1/3  # החלק הראשון של התהליך (חלוקה) הושלם
                segment_progress = segment_progress_base + (i / len(segments)) / 3  # שליש שני של התהליך
                progress_bar.progress(segment_progress, text=f"מתמלל מקטע {i+1}/{len(segments)}...")
                
                # קריאה ל-API של Gemini עם קובץ האודיו כנספח
                raw_text = transcribe_with_gemini(api_key, model, segment_prompt, audio_content, progress_bar)
                
                # אומדן טוקנים על סמך משך האודיו (אומדן גס)
                segment_duration_seconds = len(AudioSegment.from_mp3(segment_file)) / 1000
                estimated_tokens = int(segment_duration_seconds * 5)  # אומדן גס: 5 טוקנים לשנייה
                
                # רישום שימוש בטוקנים
                token_manager.record_usage(project_id, estimated_tokens)
                
                # שמירת התמלול הגולמי
                with open(raw_file, "w", encoding="utf-8") as f:
                    f.write(raw_text)
                
                status_text.info(f"  תמלול הושלם: {len(raw_text)} תווים")
            except Exception as e:
                error_msg = f"שגיאה בתמלול מקטע {i+1}: {str(e)}"
                status_text.error(f"  {error_msg}")
                raw_text = f"[שגיאה: {error_msg}]"
                
                # שמירת הודעת שגיאה
                with open(raw_file, "w", encoding="utf-8") as f:
                    f.write(raw_text)
        
        # שלב 2: עיבוד עם Generative AI של Gemini
        proc_file = os.path.join(temp_dir, f"processed_{i:03d}.txt")
        
        # בדיקה אם תמלול מעובד כבר קיים
        if os.path.exists(proc_file):
            with open(proc_file, "r", encoding="utf-8") as f:
                processed_text = f.read()
            status_text.info(f"  משתמש בתמלול מעובד קיים: {len(processed_text)} תווים")
        else:
            # מציאת פרויקט זמין ליצירת טקסט
            project_id = token_manager.get_available_project(project_ids)
            if not project_id:
                status_text.error("לא נמצאו פרויקטים עם מכסת טוקנים זמינה. נסה שוב מחר.")
                return None
            
            try:
                status_text.info(f"  משתמש בפרויקט {project_id} לעיבוד טקסט עם מודל: {model}")
                
                # יצירת הודעת מערכת ספציפית למקטע - פשוט יותר כעת ללא מבנה פורמלי
                if i == 0:
                    # מקטע ראשון
                    system_message = (
                        f"{base_transcription_prompt}\n\n"
                        f"זהו החלק הראשון של השיעור. עבד את הטקסט הגולמי לתמלול נקי ומדויק."
                    )
                elif i == len(segments) - 1:
                    # מקטע אחרון
                    system_message = (
                        f"{base_transcription_prompt}\n\n"
                        f"זהו החלק האחרון של השיעור (חלק {i+1} מתוך {len(segments)}). "
                        f"עבד את הטקסט הגולמי לתמלול נקי ומדויק."
                    )
                else:
                    # מקטע אמצעי
                    system_message = (
                        f"{base_transcription_prompt}\n\n"
                        f"זהו חלק אמצעי של השיעור (חלק {i+1} מתוך {len(segments)}). "
                        f"עבד את הטקסט הגולמי לתמלול נקי ומדויק."
                    )
                
                # הכנת הפרומפט
                prompt = f"{system_message}\n\nטקסט גולמי לעיבוד:\n{raw_text}"
                
                # אומדן ספירת טוקנים (אומדן גס: 1.5 טוקנים לתו)
                estimated_input_tokens = int(len(prompt) * 1.5)
                estimated_output_tokens = int(len(raw_text) * 2)  # פלט עשוי להיות גדול יותר בגלל פורמט
                estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
                
                # עדכון מד התקדמות
                processing_progress_base = 2/3  # שני חלקים ראשונים של התהליך (חלוקה ותמלול) הושלמו
                processing_progress = processing_progress_base + (i / len(segments)) / 3  # שליש אחרון של התהליך
                progress_bar.progress(processing_progress, text=f"מעבד תמלול מקטע {i+1}/{len(segments)}...")
                
                # קריאה ישירה ל-API של Gemini
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 8192
                    }
                }
                
                response = requests.post(gemini_url, json=payload)
                
                if response.status_code != 200:
                    raise Exception(f"בקשת API נכשלה עם קוד {response.status_code}: {response.text}")
                
                response_data = response.json()
                processed_text = response_data["candidates"][0]["content"]["parts"][0]["text"]
                
                # רישום שימוש בטוקנים
                token_manager.record_usage(project_id, estimated_total_tokens)
                
                # שמירת טקסט מעובד
                with open(proc_file, "w", encoding="utf-8") as f:
                    f.write(processed_text)
                
                status_text.info(f"  עיבוד הושלם: {len(processed_text)} תווים")
                
            except Exception as e:
                error_msg = f"שגיאה בעיבוד מקטע {i+1} עם LLM: {str(e)}"
                status_text.error(f"  {error_msg}")
                processed_text = f"[שגיאה: {error_msg}]\n\n{raw_text}"
                
                # שמירת הודעת שגיאה וטקסט גולמי כחלופה
                with open(proc_file, "w", encoding="utf-8") as f:
                    f.write(processed_text)
        
        processed_transcriptions.append(processed_text)
        
        # השהיה למניעת מגבלות קצב
        if i < len(segments) - 1:
            status_text.info("  השהיה למניעת מגבלות קצב...")
            time.sleep(2)
    
    return processed_transcriptions


def combine_transcriptions(processed_transcriptions, progress_bar, status_text):
    """שילוב תמלולים מעובדים למסמך אחד קוהרנטי."""
    status_text.info("\nמשלב תמלולים...")
    progress_bar.progress(0.95, text="משלב את כל המקטעים...")
    
    if not processed_transcriptions:
        status_text.error("אין תמלולים זמינים לשילוב")
        return None
    
    # עבור מקטע יחיד, פשוט להשתמש בו ישירות
    if len(processed_transcriptions) == 1:
        combined_text = processed_transcriptions[0]
    else:
        # מקטע ראשון כנקודת התחלה
        combined_text = processed_transcriptions[0]
        
        # עיבוד מקטעים נוספים - גישה פשוטה יותר ללא זיהוי מקטעים
        for i, segment in enumerate(processed_transcriptions[1:], 1):
            status_text.info(f"  משלב מקטע {i+1}/{len(processed_transcriptions)}...")
            
            # ניקוי שורות חוזרות בתחילה שעשויות לחפוף עם המקטע הקודם
            segment_text = segment.strip()
            
            # הוספת מעבר פסקה אם צריך
            if not combined_text.endswith('\n\n'):
                combined_text += '\n\n'
                
            # פשוט הוספת המקטע
            combined_text += segment_text
    
    status_text.info(f"  אורך כולל: {len(combined_text)} תווים")
    return combined_text


def run_transcription_app():
    """הפונקציה הראשית להפעלת האפליקציה"""
    
    # סרגל צד עם הגדרות
    st.sidebar.header("הגדרות תמלול")
    
    # הגדרות API
    api_key = st.sidebar.text_input("מפתח API של Google AI Studio", type="password")
    projects = st.sidebar.text_input("מזהי פרויקטים של Google Cloud (מופרדים בפסיקים)", 
                                     value="project-1,project-2")
    model = st.sidebar.selectbox("מודל Google AI", 
                                ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"], 
                                index=0)
    
    # הגדרות מקטעים
    with st.sidebar.expander("הגדרות מתקדמות"):
        segment_length = st.number_input("אורך מקטע מקסימלי (דקות)", 
                                        min_value=1, max_value=60, value=25)
        overlap = st.number_input("חפיפה בין מקטעים (שניות)", 
                                 min_value=0, max_value=300, value=30)
    
    # מידע על שימוש
    st.sidebar.markdown("---")
    st.sidebar.markdown("""
    ### כיצד להשתמש
    1. הזן מפתח API של Google AI Studio
    2. הזן מזהי פרויקטים (לניהול מכסות טוקנים)
    3. העלה קובץ MP3
    4. התאם את הפרומפט לפי הצורך
    5. לחץ על "תמלל" והמתן לסיום התהליך
    6. הורד את התמלול המלא בסיום
    """)
    
    # Expander לפרומפט מותאם אישית
    with st.expander("פרומפט מותאם אישית לתמלול", expanded=False):
        st.markdown("""
        כאן תוכל להתאים את ההוראות שיישלחו למודל ה-AI לתמלול. 
        ניתן להשאיר ריק כדי להשתמש בפרומפט ברירת המחדל שמותאם לתמלול שיעורי תורה.
        """)
        
        default_prompt = """# תפקידך הוא תמלול מקצועי של שיעורי תורה עם דגש על דיוק בפרטים
## מטרה
אתה מתמלל מקצועי המתמחה בתמלול שיעורי תורה בעברית. עליך לייצר טקסט מדויק במיוחד תוך שימוש בהקשר להבנה נכונה של מילים, שמות ומונחים.

## הנחיות לדיוק מבוסס הקשר
### שמות ומושגים
- הקדש תשומת לב מיוחדת לשמות של רבנים, פרשנים, ספרים, וחכמי תורה
- השתמש בהקשר השיעור כדי לזהות נכון שמות ומונחים שנשמעים לא ברורים
- כשאתה נתקל במילים לא ברורות, התייחס להקשר המשפט, נושא השיחה, והטרמינולוגיה המתאימה
- היה זהיר במיוחד עם מילים הומופוניות בעברית (מילים שנשמעות דומה) ובחר את המשמעות הנכונה על פי ההקשר
- עבור מונחים מקצועיים (מונחי הלכה, מושגי ישיבה וכו'), השתמש בידע שלך כדי לזהות אותם במדויק

### דיוק בציטוטים מהמקורות
- היה מדויק במיוחד בציטוטי פסוקים מהתנ"ך
- הקפד על דיוק בציטוטים מחז"ל, גמרא, משנה והלכה
- סמן ציטוטים במירכאות ותקן שגיאות קלות בציטוט אם ישנן

### הבחנה בין דוברים
- הבחן בבירור בין דברי הרב לשאלות הקהל
- סמן שאלות מהקהל בפורמט: [שאלה מהקהל]: תוכן השאלה
- סמן את תשובת הרב בפורמט: [הרב]: תוכן התשובה

## מה לא לעשות
- אל תנחש שמות או מונחים כשאתה לא בטוח, במקום זאת השתמש בהקשר להבנה טובה יותר
- אל תוסיף פרשנות או הסברים משלך
- אל תשנה את סגנון הדיבור של הרב
"""
        
        custom_prompt = st.text_area("הכנס פרומפט מותאם אישית", 
                                    value=default_prompt, 
                                    height=300)
    
    # אזור העלאת קובץ
    st.markdown("## העלאת קובץ אודיו")
    uploaded_file = st.file_uploader("בחר קובץ MP3 לתמלול", type=["mp3"])
    
    if uploaded_file is not None:
        st.audio(uploaded_file, format="audio/mp3")
        
        # כפתור תמלול
        if st.button("תמלל", type="primary"):
            if not api_key:
                st.error("נא להזין מפתח API של Google AI Studio")
            elif not projects:
                st.error("נא להזין לפחות מזהה פרויקט אחד")
            else:
                # יצירת אזורים להצגת התקדמות
                status_container = st.container()
                progress_bar = st.progress(0, text="מתחיל...")
                
                with status_container:
                    status_text = st.empty()
                
                # הפעלת התמלול בתהליך נפרד
                result = process_audio(
                    uploaded_file, api_key, projects, model,
                    segment_length, overlap, custom_prompt,
                    progress_bar, status_text
                )
                
                if result:
                    st.markdown("## תוצאות התמלול")
                    st.text_area("תמלול מלא", value=result, height=500)
                    
                    # הורדת קובץ
                    st.download_button(
                        label="הורד כקובץ טקסט",
                        data=result,
                        file_name=f"{uploaded_file.name.split('.')[0]}_transcription.txt",
                        mime="text/plain"
                    )

if __name__ == "__main__":
    run_transcription_app()