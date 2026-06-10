import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.llm import QwenClient
from app.workflow import HireWorkflow


def main() -> None:
    jd = (ROOT / "samples" / "jd_ai_business.txt").read_text(encoding="utf-8")
    resumes = [
        {"filename": path.name, "text": path.read_text(encoding="utf-8")}
        for path in sorted((ROOT / "samples" / "resumes").glob("*.txt"))
    ]
    result = HireWorkflow(QwenClient(Settings(ai_mode="off"))).run(jd, resumes)
    print(f"岗位: {result.job.title}")
    for index, candidate in enumerate(result.candidates, 1):
        print(
            f"{index}. {candidate.profile.name} | "
            f"{candidate.score} | {candidate.recommendation} | "
            f"题目 {len(candidate.interview_questions)} | "
            f"追问 {len(candidate.follow_up_questions)}"
        )


if __name__ == "__main__":
    main()
