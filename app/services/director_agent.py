import os
import re
import json
from typing import Dict, Any, List, Optional
from app.config import GEMINI_API_KEY


class VideoDirectorAgent:
    """
    Intelligent AI Video Director and Production Agent:
    - Analyzes story and technical scripts
    - Automatically selects the optimal media production method per scene (9 media types)
    - Builds and maintains the Character and Environment Bible for visual consistency
    - Chains story context across scenes to preserve narrative and visual continuity
    - Generates Motion-First prompts (Subject, Action, Motion, Environment, Camera, Timing)
    - Detects visual emphasis keywords and calculates dynamic scene pacing
    """

    MEDIA_TYPES = [
        "real_ai_video",       # Kinetic physical character motion, animals, nature, walking
        "image_to_video",      # Animation anchored to character reference frame
        "screen_recording",    # Real Windows UI, Desktop settings, terminal executions
        "browser_automation",  # Controlled Playwright/Chrome browser navigation & interactions
        "diagram",             # Architectural flowcharts, API sequences, visual explanations
        "ai_image",            # Still keyframe cinematic shot with gentle pan/zoom
        "user_video",          # User uploaded video clip
        "user_image",          # User uploaded photo
        "title_scene",         # Title / chapter transition card
    ]

    def direct_production(
        self,
        story_text: str,
        title: str = "",
        animation_style: str = "pixar",
        preferred_mode: str = "auto"
    ) -> Dict[str, Any]:
        """
        Main entry point for the Director Agent.
        Returns the full Director Plan:
        - Character/Environment Bible
        - Scene-by-scene production specs with media_type, motion_prompt, camera_prompt, emphasis_keywords, etc.
        """
        story_text = story_text.strip()
        if not story_text:
            story_text = "A modern technical walkthrough demonstrating automated testing and system setup."

        # Step 1: Use Gemini 2.5 Flash as the Executive Film & Technical Director if available
        if GEMINI_API_KEY:
            try:
                plan = self._gemini_direct_plan(story_text, title, animation_style, preferred_mode)
                if plan and "scenes" in plan and len(plan["scenes"]) > 0:
                    return self._validate_and_normalize_plan(plan, story_text, animation_style)
            except Exception as e:
                print(f"[director_agent] Gemini Director notice ({e}), using autonomous rule-based director...")

        # Step 2: Autonomous Rule-Based Director Engine
        return self._rule_based_director(story_text, title, animation_style, preferred_mode)

    def _gemini_direct_plan(
        self,
        story_text: str,
        title: str,
        animation_style: str,
        preferred_mode: str
    ) -> Optional[Dict[str, Any]]:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        director_prompt = f"""You are an elite AI Video Director and Executive Production Agent.
Analyze the user's script/story below and produce a complete cinematic & technical production plan.

Story Title: "{title or 'AI Production'}"
Visual Style: {animation_style}
Preferred Production Override: {preferred_mode}
Script / Story:
\"\"\"{story_text}\"\"\"

YOUR RESPONSIBILITIES:
1. CREATE A CHARACTER & ENVIRONMENT BIBLE:
   Extract persistent visual traits to guarantee visual consistency across every single scene.
   - character: name/role, exact appearance, clothing colors, hair, ethnicity/species, distinctive traits.
   - environment: primary location, architecture, lighting, color palette, atmosphere.

2. MULTI-MODAL SCENE DECISION MATRIX:
   Do NOT use the same production method for every scene! Select the best production method per scene from:
   - "real_ai_video": For narrative scenes requiring real physical character/animal walking, gestures, environmental breeze.
   - "image_to_video": For scenes starting from an established character visual and animating temporal motion.
   - "screen_recording": For technical demonstrations showing Windows Settings, Desktop, Terminal, or OS actions.
   - "browser_automation": For web tutorials navigating URLs, clicking web elements, form typing.
   - "diagram": For abstract concept explanations, architecture flowcharts (e.g. Client -> API -> Database, or troubleshooting decision trees).
   - "title_scene": For punchy video intro titles or chapter markers.
   - "ai_image": For dramatic still establishing keyframes.

3. MOTION-FIRST PROMPTS:
   Every video scene MUST have a "motion_prompt" describing:
   [Subject] Who/what is visible.
   [Action] What happens.
   [Motion] How subject physically moves.
   [Environment Motion] Wind, background elements, lighting changes.
   [Camera] Specific camera path (tracking shot, pan, dolly in, low angle).
   [Timing] Beginning vs middle vs ending movement.

4. STORY CONTINUITY:
   Track what happened in the previous scene, what happens now, and what happens next.

5. VISUAL EMPHASIS:
   Identify 1-3 key action words or terms from the narration to highlight visually (e.g., "Advanced Options", "playwright test", "token").

Return ONLY a raw valid JSON object with EXACTLY this structure:
{{
  "title": "{title or 'AI Production'}",
  "bible": {{
    "character": {{
      "identity": "...",
      "clothing": "...",
      "hair_or_features": "...",
      "palette": "..."
    }},
    "environment": {{
      "location": "...",
      "lighting": "...",
      "palette": "...",
      "atmosphere": "..."
    }}
  }},
  "scenes": [
    {{
      "scene_id": 1,
      "media_type": "real_ai_video | screen_recording | browser_automation | diagram | image_to_video | title_scene | ai_image",
      "reason": "Why this media type is optimal for this scene",
      "narration": "1-3 natural spoken sentences for this scene beat",
      "visual_prompt": "Detailed visual description incorporating the character and environment bible",
      "motion_prompt": "Full motion-first prompt with Subject, Action, Motion, Environment motion, Camera, and Timing",
      "camera_motion": "tracking_shot | zoom_in | zoom_out | pan_left | pan_right | static",
      "mood": "magical | exciting | focused | triumphant | serious | peaceful",
      "emphasis_keywords": ["key term 1", "key term 2"],
      "diagram_spec": {{
        "type": "flowchart | architecture | sequence",
        "nodes": ["Step A", "Step B", "Step C"],
        "arrows": ["A -> B", "B -> C"]
      }},
      "tutorial_spec": {{
        "platform": "windows | chrome | terminal | playwright | sql | jmeter",
        "command_or_action": "..."
      }}
    }}
  ]
}}
Do NOT include markdown fences, return pure JSON."""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=director_prompt
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw.strip())

    def _rule_based_director(
        self,
        story_text: str,
        title: str,
        animation_style: str,
        preferred_mode: str
    ) -> Dict[str, Any]:
        """
        Autonomous rule-based director when external LLM is not active.
        """
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', story_text) if p.strip()]
        raw_sentences = []
        for p in paragraphs:
            sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', p) if len(s.strip()) > 3]
            if sents:
                raw_sentences.extend(sents)
            else:
                raw_sentences.append(p)

        if not raw_sentences:
            raw_sentences = [story_text]

        # Detect technical tutorial vs narrative fiction
        full_lower = story_text.lower()
        is_technical = any(k in full_lower for k in [
            "windows", "chrome", "playwright", "selenium", "jmeter", "sql", "api",
            "test", "terminal", "command", "browser", "click", "settings", "fix",
            "error", "troubleshoot", "install", "code", "run", "query"
        ])

        # Generate character / environment bible
        if is_technical:
            bible = {
                "character": {
                    "identity": "Expert Software Engineer & System Specialist",
                    "clothing": "Clean modern tech attire, navy blue polo",
                    "hair_or_features": "Neat professional appearance, focused expression",
                    "palette": "Cool blues, deep navy, slate gray"
                },
                "environment": {
                    "location": "Modern high-tech software development workstation and Windows OS environment",
                    "lighting": "Clean studio lighting, subtle ambient cyan backlighting",
                    "palette": "Dark slate, electric blue, clean white",
                    "atmosphere": "Professional, efficient, analytical"
                }
            }
        else:
            bible = {
                "character": {
                    "identity": f"Main protagonist ({title or 'hero'}) in {animation_style} style",
                    "clothing": "Vibrant signature cinematic outfit with distinctive colors",
                    "hair_or_features": "Expressive eyes, charming detailed animated features",
                    "palette": "Warm amber, gold, rich storybook tones"
                },
                "environment": {
                    "location": "Cinematic animated world matching story theme",
                    "lighting": "Volumetric sunlight, atmospheric rim lighting",
                    "palette": "Lush vibrant colors, natural cinematic lighting",
                    "atmosphere": "Wondrous, cinematic, immersive"
                }
            }

        scenes = []
        curr_narr = []
        curr_words = 0

        for sent in raw_sentences:
            w_count = len(sent.split())
            if curr_words + w_count > 28 and curr_narr:
                scenes.append(" ".join(curr_narr))
                curr_narr = [sent]
                curr_words = w_count
            else:
                curr_narr.append(sent)
                curr_words += w_count

        if curr_narr:
            scenes.append(" ".join(curr_narr))

        if len(scenes) == 1 and len(scenes[0].split()) > 20:
            half = len(raw_sentences) // 2
            if half > 0:
                scenes = [" ".join(raw_sentences[:half]), " ".join(raw_sentences[half:])]

        directed_scenes = []
        prev_scene_summary = ""

        for idx, narr in enumerate(scenes):
            scene_id = idx + 1
            n_low = narr.lower()

            if preferred_mode in ["real_ai_video", "screen_recording", "image_animation"]:
                media_type = preferred_mode
                reason = f"User preference override: {preferred_mode}"
            elif is_technical:
                if idx == 0:
                    media_type = "title_scene" if len(narr.split()) < 12 else "screen_recording"
                    reason = "Introduction / technical overview"
                elif any(k in n_low for k in ["why", "architecture", "diagram", "concept", "flow", "server", "database", "request", "response"]):
                    media_type = "diagram"
                    reason = "Architectural explanation & process flowchart"
                elif any(k in n_low for k in ["chrome", "browser", "website", "url", "navigate", "portal"]):
                    media_type = "browser_automation"
                    reason = "Live browser navigation & web interaction"
                elif any(k in n_low for k in ["terminal", "test", "playwright", "command", "bash", "sql", "jmeter"]):
                    media_type = "screen_recording"
                    reason = "Live CLI execution & automated test verification"
                else:
                    media_type = "screen_recording"
                    reason = "Windows desktop & system settings demonstration"
            else:
                if idx == 0 and len(narr.split()) < 10:
                    media_type = "title_scene"
                    reason = "Story opening title sequence"
                elif idx % 2 == 0:
                    media_type = "real_ai_video"
                    reason = "Dynamic character movement and environmental action"
                else:
                    media_type = "image_to_video"
                    reason = "Continuous scene motion anchored to established character frame"

            emphasis_keywords = self._extract_emphasis_keywords(narr)
            clean_subject = narr.split('.')[0][:80]
            motion_prompt = (
                f"[Subject: {clean_subject} adhering to character bible ({bible['character']['identity']})]. "
                f"[Action: Character physically moves with lifelike kinetics]. "
                f"[Motion: Smooth temporal progression, natural strides, responsive physics]. "
                f"[Environment Motion: Gentle atmosphere motion, atmospheric particles in {bible['environment']['location']}]. "
                f"[Camera: Dynamic cinematic tracking shot, steady camera drift]. "
                f"[Timing: Smooth acceleration into scene, steady movement across frame, resting finish]."
            )

            context_note = f"Following from previous beat: '{prev_scene_summary}'" if prev_scene_summary else "Opening narrative beat."
            prev_scene_summary = narr[:45]
            visual_prompt = f"{clean_subject}, in {bible['environment']['location']}, {bible['character']['clothing']}, {bible['environment']['lighting']}, {animation_style} style, masterpiece"

            directed_scenes.append({
                "scene_id": scene_id,
                "media_type": media_type,
                "reason": reason,
                "narration": narr,
                "visual_prompt": visual_prompt,
                "motion_prompt": motion_prompt,
                "camera_motion": "tracking_shot" if idx % 2 == 0 else "zoom_in",
                "mood": "focused" if is_technical else "magical",
                "emphasis_keywords": emphasis_keywords,
                "story_context": context_note,
                "diagram_spec": {
                    "type": "flowchart",
                    "nodes": ["Input Request", "Execution Engine", "Verified Output"],
                    "arrows": ["Request -> Engine", "Engine -> Output"]
                } if media_type == "diagram" else None,
                "tutorial_spec": {
                    "platform": "chrome" if "chrome" in n_low else ("playwright" if "playwright" in n_low else "windows"),
                    "action": "execute_test_suite" if "test" in n_low else "configure_setting"
                } if media_type in ["screen_recording", "browser_automation"] else None
            })

        return {
            "title": title or "AI Video Production",
            "bible": bible,
            "scenes": directed_scenes
        }

    def _extract_emphasis_keywords(self, text: str) -> List[str]:
        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
        words = []
        for q in quoted:
            val = q[0] or q[1]
            if len(val) < 25:
                words.append(val)

        triggers = [
            "Advanced Options", "Playwright", "Terminal", "Settings", "System",
            "PostgreSQL", "SQL", "JMeter", "Run", "Click", "Login", "Assertion",
            "Administrator", "Environment Variables", "Registry", "Troubleshoot"
        ]
        t_low = text.lower()
        for trig in triggers:
            if trig.lower() in t_low and trig not in words:
                words.append(trig)

        return words[:3]

    def _validate_and_normalize_plan(
        self,
        plan: Dict[str, Any],
        fallback_story: str,
        animation_style: str
    ) -> Dict[str, Any]:
        if "bible" not in plan or not isinstance(plan["bible"], dict):
            plan["bible"] = {
                "character": {"identity": "Main subject", "clothing": "Signature attire"},
                "environment": {"location": "Cinematic setting", "lighting": "Atmospheric"}
            }

        scenes = plan.get("scenes", [])
        if not scenes or not isinstance(scenes, list):
            return self._rule_based_director(fallback_story, plan.get("title", ""), animation_style, "auto")

        for idx, sc in enumerate(scenes):
            sc["scene_id"] = idx + 1
            if sc.get("media_type") not in self.MEDIA_TYPES:
                sc["media_type"] = "real_ai_video"
            if not sc.get("narration"):
                sc["narration"] = f"Scene {idx + 1} narrative beat."
            if not sc.get("motion_prompt"):
                sc["motion_prompt"] = f"{sc.get('visual_prompt', '')}, continuous physical character motion, fluid movement, cinematic camera tracking"
            if not sc.get("camera_motion"):
                sc["camera_motion"] = "tracking_shot"
            if not sc.get("mood"):
                sc["mood"] = "magical"
            if "emphasis_keywords" not in sc or not isinstance(sc["emphasis_keywords"], list):
                sc["emphasis_keywords"] = self._extract_emphasis_keywords(sc["narration"])

        return plan
