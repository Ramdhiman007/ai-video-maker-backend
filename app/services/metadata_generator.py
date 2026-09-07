import re
from typing import List, Dict, Any


class YouTubeMetadataGenerator:
    """
    Generates YouTube-ready publishing metadata:
    - High CTR SEO Title
    - Structured description with automatic chapter timestamps for every scene
    - Hashtags & YouTube video tags
    """

    def generate_metadata(
        self,
        title: str,
        scenes: List[Dict[str, Any]],
        style: str = "tutorial",
        total_duration: float = 60.0
    ) -> Dict[str, Any]:
        clean_title = title.strip() or "AI Video Production"

        # Generate timestamps for each scene
        chapters = []
        curr_time = 0.0
        for idx, sc in enumerate(scenes):
            mins = int(curr_time // 60)
            secs = int(curr_time % 60)
            time_str = f"{mins:02d}:{secs:02d}"

            # Chapter heading
            raw_text = sc.get("narration", "")
            words = raw_text.split()
            label = " ".join(words[:5]).title() if words else f"Scene {idx + 1}"
            label = re.sub(r'[^\w\s-]', '', label)
            chapters.append(f"{time_str} - {label}")

            curr_time += sc.get("duration", 5.0)

        chapters_text = "\n".join(chapters)

        # Build Description
        description = f"""{clean_title}

In this complete step-by-step walkthrough, we cover everything you need to know with direct demonstrations, live assertions, and clear explanations.

TIMESTAMPS:
{chapters_text}

KEY HIGHLIGHTS:
- Complete automated walkthrough & live verification
- Real-time testing assertions and system configuration
- Step-by-step troubleshooting workflow

🔔 Subscribe for more in-depth software testing, automation, and tech tutorials!

#SoftwareTesting #Tutorial #Playwright #Automation #Windows #TechHelp
"""

        # Generate Tags
        tags = [
            "ai video", "software testing", "automation", "tech tutorial",
            "step by step", "how to", "troubleshooting", "guide 2026"
        ]
        for w in clean_title.lower().split():
            if len(w) > 3 and w not in tags:
                tags.append(w)

        return {
            "title": clean_title,
            "description": description,
            "tags": tags[:15],
            "chapters": chapters,
            "hashtags": ["#SoftwareTesting", "#Automation", "#TechTutorial", "#Playwright"]
        }
