# Claude Projects

Index of all projects in this workspace.

---

## Projects

| # | Project | Description | Stack | URL |
|---|---------|-------------|-------|-----|
| 1 | [ASTAR_Enterprise_AI_Adoption](projects/ASTAR_Enterprise_AI_Adoption/) | Proposal for enterprise-wide AI tools deployment across A*STAR I2R & IHPC (650 researchers). Covers AWS, Microsoft, Google tools across 4 layers with cost scenarios. | Python + docx | Local |
| 2 | [Snake_Game](projects/Snake_Game/) | Classic Snake game packaged as a standalone macOS app. | Python + pygame + PyInstaller | Local |
| 3 | [Super_Mario](projects/Super_Mario/) | Super Mario-style side-scrolling platformer with Goombas, coins, platforms, pits, and a flag goal. Packaged as a standalone macOS app. | Python + pygame + PyInstaller | Local |
| 4 | [photo-app](projects/photo-app/) | AI-powered photo search app. Uses Claude Vision to index and describe photos, enabling natural language search. | FastAPI + Claude Vision | Local |
| 5 | [embodied-ai-scanner](projects/embodied-ai-scanner/) | Scrapes and aggregates Embodied AI & Humanoid Robotics news, filters relevant articles, and generates trend & weak-signal reports using Claude. | FastAPI + Claude + SQLite | [embodied-ai-scanner.onrender.com](https://embodied-ai-scanner.onrender.com) |
| 6 | [News_Summariser](projects/News_Summariser/) | Fetches science & tech news from MIT Tech Review, Science.org, and Nature.com, summarises and categorises articles into a daily digest using Claude. | Flask + Claude + SQLite | [news-summariser-s1tq.onrender.com](https://news-summariser-s1tq.onrender.com) |
| 7 | [debate-agents](projects/debate-agents/) | Two Claude agents argue opposing sides of any topic across 3 rounds, with a preliminary judge, fact-checker, and final verdict. Real-time streaming via SSE. | FastAPI + Claude + SQLite + SSE | [debate-agents.onrender.com](https://debate-agents.onrender.com) |
| 8 | [Food_Nutrition_App](projects/Food_Nutrition_App/) | iPhone PWA for food nutrition tracking. Type a meal or snap a photo — Claude analyses nutrition instantly. Includes daily goals calculator (BMR), macro tracking, and meal history. | FastAPI + Claude + PostgreSQL + PWA | [food-nutrition-app-x2qf.onrender.com](https://food-nutrition-app-x2qf.onrender.com) |

---

## Structure

```
Claude_Projects/
├── README.md                      ← this file (project index)
└── projects/
    └── <ProjectName>/
        └── ...project files...
```
