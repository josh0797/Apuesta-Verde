{
  "brand": {
    "name": "Value Bet Intelligence",
    "positioning": [
      "Bloomberg-terminal clarity + sportsbook speed",
      "Discipline-first: risk awareness, transparency, no-casino vibes",
      "Data-dense, fast scanning, honest empty states"
    ],
    "voice": {
      "tone": ["analytical", "calm", "direct", "bilingual ES/EN"],
      "copy_rules": [
        "Avoid hype words (jackpot, insane, crazy).",
        "Prefer analyst language: 'edge', 'risk', 'volatility', 'confidence', 'line movement'.",
        "Empty state is a feature: reinforce discipline."
      ]
    }
  },

  "visual_style": {
    "style_keywords": [
      "dark sportsbook modern",
      "terminal grid",
      "neon accents (small + purposeful)",
      "bento + table hybrid",
      "micro-glow borders",
      "monospace numerics"
    ],
    "layout_principles": [
      "Mobile-first: stacked cards -> desktop: 12-col grid with sticky right rail",
      "F-pattern reading: left-aligned headings, dense tables with strong row separators",
      "Use color as status, not decoration (green/value, red/risk, amber/high confidence)",
      "Degraded states must be explicit (stale/missing badges + skeletons)"
    ]
  },

  "typography": {
    "google_fonts": {
      "heading": {
        "family": "Space Grotesk",
        "fallback": "ui-sans-serif, system-ui",
        "weights": [500, 600, 700]
      },
      "body": {
        "family": "Inter",
        "fallback": "ui-sans-serif, system-ui",
        "weights": [400, 500, 600]
      },
      "numeric": {
        "family": "IBM Plex Mono",
        "fallback": "ui-monospace, SFMono-Regular",
        "weights": [400, 500, 600]
      }
    },
    "tailwind_mapping": {
      "h1": "text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight",
      "h2": "text-base md:text-lg font-medium text-muted-foreground",
      "section_title": "text-lg md:text-xl font-semibold",
      "card_title": "text-sm font-semibold tracking-wide",
      "body": "text-sm md:text-base leading-relaxed",
      "small": "text-xs text-muted-foreground",
      "numeric": "font-mono tabular-nums"
    },
    "number_formatting": [
      "Odds, confidence, xG, ROI, deltas must use tabular-nums + font-mono.",
      "Use fixed decimals for odds (e.g., 1.85) and xG (e.g., 0.74)."
    ]
  },

  "color_system": {
    "mode": "dark-default",
    "tokens_css": {
      "note": "Update /app/frontend/src/index.css :root and .dark tokens. App should run with .dark on html/body by default.",
      "css": ":root{\n  --background: 220 26% 6%;\n  --foreground: 210 40% 98%;\n  --card: 220 24% 9%;\n  --card-foreground: 210 40% 98%;\n  --popover: 220 24% 9%;\n  --popover-foreground: 210 40% 98%;\n  --primary: 160 84% 45%;\n  --primary-foreground: 220 26% 6%;\n  --secondary: 220 18% 14%;\n  --secondary-foreground: 210 40% 98%;\n  --muted: 220 18% 14%;\n  --muted-foreground: 215 16% 70%;\n  --accent: 200 92% 55%;\n  --accent-foreground: 220 26% 6%;\n  --destructive: 0 84% 60%;\n  --destructive-foreground: 210 40% 98%;\n  --border: 220 16% 18%;\n  --input: 220 16% 18%;\n  --ring: 160 84% 45%;\n  --radius: 0.75rem;\n  --chart-1: 160 84% 45%;\n  --chart-2: 45 96% 55%;\n  --chart-3: 0 84% 60%;\n  --chart-4: 200 92% 55%;\n  --chart-5: 215 16% 70%;\n}\n.dark{\n  --background: 220 26% 6%;\n  --foreground: 210 40% 98%;\n  --card: 220 24% 9%;\n  --card-foreground: 210 40% 98%;\n  --popover: 220 24% 9%;\n  --popover-foreground: 210 40% 98%;\n  --primary: 160 84% 45%;\n  --primary-foreground: 220 26% 6%;\n  --secondary: 220 18% 14%;\n  --secondary-foreground: 210 40% 98%;\n  --muted: 220 18% 14%;\n  --muted-foreground: 215 16% 70%;\n  --accent: 200 92% 55%;\n  --accent-foreground: 220 26% 6%;\n  --destructive: 0 84% 60%;\n  --destructive-foreground: 210 40% 98%;\n  --border: 220 16% 18%;\n  --input: 220 16% 18%;\n  --ring: 160 84% 45%;\n }"
    },
    "semantic_colors": {
      "value_positive": {
        "name": "Neon Mint",
        "hex": "#2EE59D",
        "usage": "value edge, wins, positive deltas"
      },
      "risk_negative": {
        "name": "Signal Red",
        "hex": "#FF5A67",
        "usage": "risks, losses, trap warnings"
      },
      "confidence_high": {
        "name": "Analyst Amber",
        "hex": "#F5B301",
        "usage": "Alta confidence, premium highlights"
      },
      "info_cyan": {
        "name": "Terminal Cyan",
        "hex": "#35D3FF",
        "usage": "info, selected states, links"
      },
      "surface_bg": { "hex": "#0B0F14" },
      "surface_1": { "hex": "#111827" },
      "surface_2": { "hex": "#172033" },
      "border": { "hex": "#2A3441" },
      "text": { "hex": "#F3F7FB" },
      "text_muted": { "hex": "#9AA4B2" }
    },
    "motivation_badges_1_to_5": {
      "rule": "Use color + label; do NOT rely on emoji. Provide 5 discrete levels.",
      "levels": [
        { "level": 1, "label": "Muy baja / Very low", "bg": "bg-red-500/15", "text": "text-red-300", "border": "border-red-500/30" },
        { "level": 2, "label": "Baja / Low", "bg": "bg-orange-500/15", "text": "text-orange-300", "border": "border-orange-500/30" },
        { "level": 3, "label": "Media / Medium", "bg": "bg-yellow-500/15", "text": "text-yellow-200", "border": "border-yellow-500/30" },
        { "level": 4, "label": "Alta / High", "bg": "bg-emerald-500/15", "text": "text-emerald-200", "border": "border-emerald-500/30" },
        { "level": 5, "label": "Máxima / Max", "bg": "bg-cyan-500/15", "text": "text-cyan-200", "border": "border-cyan-500/30" }
      ]
    },
    "data_freshness": {
      "fresh": { "bg": "bg-emerald-500/15", "text": "text-emerald-200", "border": "border-emerald-500/30" },
      "stale": { "bg": "bg-amber-500/15", "text": "text-amber-200", "border": "border-amber-500/30" },
      "missing": { "bg": "bg-slate-500/15", "text": "text-slate-200", "border": "border-slate-500/30" }
    }
  },

  "gradients_and_textures": {
    "compliance": {
      "gradient_restriction_rule": [
        "NEVER use dark/saturated gradient combos (e.g., purple/pink) on any UI element.",
        "NEVER let gradients cover more than 20% of the viewport.",
        "NEVER apply gradients to text-heavy content or reading areas.",
        "NEVER use gradients on small UI elements (<100px width).",
        "NEVER stack multiple gradient layers in the same viewport.",
        "IF gradient area exceeds 20% of viewport OR impacts readability THEN fallback to solid colors."
      ]
    },
    "allowed_usage": [
      "Hero/login header background only (top 15–20vh)",
      "Decorative corner glows behind charts (blurred, low opacity)",
      "Selected tab underline glow (thin)"
    ],
    "approved_gradients": [
      {
        "name": "Terminal Glow",
        "css": "radial-gradient(600px circle at 20% 0%, rgba(46,229,157,0.18), transparent 55%), radial-gradient(700px circle at 80% 10%, rgba(245,179,1,0.14), transparent 60%)",
        "usage": "login + dashboard top header backdrop"
      }
    ],
    "noise_overlay": {
      "css_snippet": ".noise::before{content:'';position:absolute;inset:0;background-image:url('https://images.pexels.com/photos/7641028/pexels-photo-7641028.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940');opacity:.06;mix-blend-mode:overlay;pointer-events:none;border-radius:inherit;}",
      "rule": "Use very low opacity (0.04–0.08). Apply to large containers only."
    }
  },

  "grid_and_spacing": {
    "container": {
      "max_width": "max-w-7xl",
      "padding": "px-4 sm:px-6 lg:px-8",
      "vertical_rhythm": "space-y-6 md:space-y-8"
    },
    "desktop_layout": {
      "grid": "lg:grid lg:grid-cols-12 lg:gap-6",
      "main": "lg:col-span-8",
      "rail": "lg:col-span-4 lg:sticky lg:top-20"
    },
    "cards": {
      "radius": "rounded-xl",
      "padding": "p-4 md:p-5",
      "density": "Use compact typography but generous spacing between groups (gap-3/4)."
    }
  },

  "components": {
    "component_path": {
      "shadcn_ui": {
        "button": "/app/frontend/src/components/ui/button.jsx",
        "badge": "/app/frontend/src/components/ui/badge.jsx",
        "card": "/app/frontend/src/components/ui/card.jsx",
        "tabs": "/app/frontend/src/components/ui/tabs.jsx",
        "table": "/app/frontend/src/components/ui/table.jsx",
        "progress": "/app/frontend/src/components/ui/progress.jsx",
        "tooltip": "/app/frontend/src/components/ui/tooltip.jsx",
        "dropdown_menu": "/app/frontend/src/components/ui/dropdown-menu.jsx",
        "sheet": "/app/frontend/src/components/ui/sheet.jsx",
        "dialog": "/app/frontend/src/components/ui/dialog.jsx",
        "select": "/app/frontend/src/components/ui/select.jsx",
        "switch": "/app/frontend/src/components/ui/switch.jsx",
        "separator": "/app/frontend/src/components/ui/separator.jsx",
        "scroll_area": "/app/frontend/src/components/ui/scroll-area.jsx",
        "skeleton": "/app/frontend/src/components/ui/skeleton.jsx",
        "sonner": "/app/frontend/src/components/ui/sonner.jsx",
        "calendar": "/app/frontend/src/components/ui/calendar.jsx"
      },
      "recommended_new_components_to_create": [
        "/app/frontend/src/components/ConfidenceMeter.jsx",
        "/app/frontend/src/components/ValueEdgePill.jsx",
        "/app/frontend/src/components/MotivationBadge.jsx",
        "/app/frontend/src/components/FreshnessBadge.jsx",
        "/app/frontend/src/components/LineMovement.jsx",
        "/app/frontend/src/components/LivePulse.jsx",
        "/app/frontend/src/components/OddsComparisonTable.jsx",
        "/app/frontend/src/components/MatchCard.jsx",
        "/app/frontend/src/components/EmptyStateNoValue.jsx",
        "/app/frontend/src/components/LanguageToggle.jsx",
        "/app/frontend/src/components/StickyFilterBar.jsx"
      ]
    },

    "buttons": {
      "shape": "Professional / Corporate (radius 10–12px via --radius 0.75rem)",
      "variants": {
        "primary": {
          "use": "Generate picks / main CTA",
          "classes": "bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring",
          "micro_interaction": "hover: subtle glow via shadow + translate-y-0.5; active: scale-[0.98]"
        },
        "secondary": {
          "use": "Filters, secondary actions",
          "classes": "bg-secondary text-secondary-foreground hover:bg-secondary/80 border border-border",
          "micro_interaction": "hover: border becomes accent/40"
        },
        "ghost": {
          "use": "Table row actions, icon buttons",
          "classes": "hover:bg-white/5 text-foreground",
          "micro_interaction": "hover: show underline indicator or left border"
        },
        "danger": {
          "use": "Mark lost / delete",
          "classes": "bg-destructive text-destructive-foreground hover:bg-destructive/90"
        }
      },
      "data_testid_rule": "All buttons must include data-testid like generate-picks-button, login-google-button, mark-pick-won-button"
    },

    "badges_and_chips": {
      "confidence_group_badges": {
        "alta": "bg-amber-500/15 text-amber-200 border border-amber-500/30",
        "media": "bg-cyan-500/15 text-cyan-200 border border-cyan-500/30",
        "descartados": "bg-red-500/15 text-red-300 border border-red-500/30",
        "incompletos": "bg-slate-500/15 text-slate-200 border border-slate-500/30"
      },
      "market_allowed_badge": "bg-emerald-500/10 text-emerald-200 border border-emerald-500/25",
      "market_forbidden_badge": "bg-red-500/10 text-red-200 border border-red-500/25 line-through"
    },

    "tables": {
      "odds_comparison": {
        "pattern": "Sticky first column (bookmaker) on desktop; horizontal scroll on mobile via ScrollArea",
        "row_density": "text-xs md:text-sm",
        "classes": "w-full border-separate border-spacing-0",
        "row_hover": "hover:bg-white/3",
        "best_price_highlight": "ring-1 ring-emerald-500/40 bg-emerald-500/8"
      },
      "line_movement": {
        "pattern": "Show arrow up/down + delta; color-coded; tooltip explains movement window",
        "classes": "font-mono tabular-nums"
      }
    },

    "cards": {
      "match_card": {
        "layout": [
          "Header: teams + league + kickoff/live minute + freshness badges",
          "Middle: recommended market + odds + confidence meter + value edge pill",
          "Footer: risks chips + cash-out viability + anti-trap indicator"
        ],
        "classes": "rounded-xl bg-card border border-border/80 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]",
        "interaction": "Entire card clickable; hover shows subtle border glow (accent/20) and lifts 1px"
      }
    },

    "charts_and_meters": {
      "confidence_meter": {
        "type": "Gauge-like bar + numeric",
        "implementation": "Use Progress (shadcn) + custom ticks (0/50/78/100).",
        "color_logic": {
          "gte_78": "amber",
          "68_77": "cyan",
          "lt_68": "muted",
          "discarded": "red"
        }
      },
      "live_momentum": {
        "library": "recharts",
        "install": "npm i recharts",
        "use": "Small sparkline for momentum/xG trend in /live and /match/:id",
        "empty_state": "If no live_stats, show Skeleton + 'Datos en vivo no disponibles'"
      }
    }
  },

  "page_blueprints": {
    "global_shell": {
      "header": {
        "left": "Logo + app name + environment badge (LIVE/48H)",
        "center": "Primary nav tabs: Dashboard / Live / History / Profile",
        "right": "Language toggle (ES/EN) + user menu",
        "behavior": "Sticky header with subtle backdrop blur; on scroll add border + shadow",
        "classes": "sticky top-0 z-40 bg-background/70 backdrop-blur supports-[backdrop-filter]:bg-background/50 border-b border-border"
      },
      "sticky_filter_bar": {
        "use": "Dashboard + Live",
        "filters": ["Confidence group", "Market", "Freshness", "Anti-trap", "League"],
        "behavior": "Sticky below header; chips wrap on mobile; shows active filter count",
        "classes": "sticky top-14 z-30 bg-background/80 backdrop-blur border-b border-border"
      }
    },

    "/login": {
      "goal": "Premium + trustworthy Google auth",
      "layout": "Split-screen on desktop (left brand panel, right auth card). Single column on mobile.",
      "left_panel": [
        "Brand headline: 'Value Bet Intelligence'",
        "Subhead: 'Disciplina. Transparencia. Valor real.'",
        "3 bullets: Allowed markets only, Anti-trap detection, Track accuracy",
        "Background: Terminal Glow gradient + subtle stadium image masked"
      ],
      "auth_card": [
        "Google button (primary)",
        "Security note: 'No guardamos tu contraseña'",
        "Terms/Privacy links"
      ],
      "data_testids": ["login-google-button", "language-toggle", "login-terms-link"]
    },

    "/dashboard": {
      "hero_row": [
        "Left: 'Picks del día' + date + freshness summary",
        "Right: CTA 'Generar picks del día' + last run timestamp"
      ],
      "grouping": [
        "Alta (>=78)",
        "Media (68-77)",
        "Descartados",
        "Datos incompletos"
      ],
      "cards": "Use MatchCard; group sections are collapsible on mobile (Collapsible).",
      "empty_state": "Use EmptyStateNoValue component with strong copy."
    },

    "/live": {
      "layout": "Two-column on desktop: live list + selected match panel. Mobile: tabs (List/Details).",
      "live_row": [
        "Pulsing live dot + minute",
        "Scoreline",
        "Key stats chips: possession, shots, xG",
        "Momentum sparkline"
      ],
      "data_testids": ["live-match-row", "live-match-details-panel"]
    },

    "/match/:id": {
      "layout": "Desktop: main (analysis) + right rail (odds + actions).",
      "sections": [
        "1) Summary strip: confidence meter, recommended market, odds, anti-trap badge",
        "2) Odds snapshots: bookmaker comparison table + line movement",
        "3) Team context: form, injuries, schedule congestion, motivation scoring 1-5 per team",
        "4) Live stats (if live): possession/xG/momentum + event timeline",
        "5) Analyst reasoning paragraph",
        "6) Risks list (bulleted, severity chips)",
        "7) Cash-out viability indicator"
      ],
      "actions": [
        "Mark pick: won/lost/push",
        "Add note",
        "Copy share summary"
      ],
      "data_testids": ["match-confidence-meter", "odds-comparison-table", "mark-pick-won-button", "mark-pick-lost-button", "mark-pick-push-button"]
    },

    "/history": {
      "layout": "Top KPI strip + table of picks",
      "kpis": ["Win rate %", "Streak", "Last 10", "ROI (placeholder)"]
    },

    "/profile": {
      "layout": "Profile card + stats dashboard",
      "stats": ["Win rate", "Accuracy by confidence group", "Market breakdown", "Trap-avoidance rate (placeholder)"]
    }
  },

  "motion_and_microinteractions": {
    "library": {
      "name": "framer-motion",
      "install": "npm i framer-motion",
      "use_cases": [
        "Section entrance (fade+slide 8px)",
        "Card hover lift (translateY -1) + border glow",
        "Live pulse dot animation",
        "Collapsible group expand/collapse"
      ]
    },
    "rules": [
      "No universal transition: never use transition-all.",
      "Prefer transition-colors, transition-shadow, transition-opacity.",
      "Respect prefers-reduced-motion: disable pulsing + entrance animations."
    ],
    "snippets": {
      "card_hover": "transition-shadow transition-colors duration-200 hover:shadow-[0_0_0_1px_rgba(53,211,255,0.18),0_10px_30px_rgba(0,0,0,0.35)] hover:border-cyan-500/25",
      "live_pulse": "relative before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-2 before:w-2 before:rounded-full before:bg-emerald-400 before:shadow-[0_0_0_6px_rgba(46,229,157,0.12)] motion-safe:before:animate-pulse"
    }
  },

  "accessibility": {
    "requirements": [
      "WCAG AA contrast for text and interactive controls.",
      "Visible focus rings: focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      "Do not rely on color alone: add icons/labels for value/risk/trap.",
      "Keyboard navigation for tabs, tables, dialogs, dropdowns.",
      "All interactive + key informational elements MUST include data-testid (kebab-case)."
    ],
    "table_a11y": [
      "Use <Table> semantics from shadcn.",
      "Provide aria-label for odds tables and tooltips for abbreviations (DNB, 1X2)."
    ]
  },

  "performance": {
    "dashboard": [
      "Use Skeleton for loading states; avoid layout shift.",
      "Virtualize long tables if needed (optional later).",
      "Prefer memoized MatchCard; keep charts tiny (sparklines)."
    ]
  },

  "image_urls": {
    "login_background": [
      {
        "url": "https://images.unsplash.com/photo-1556056504-5c7696c4c28d?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAxODF8MHwxfHNlYXJjaHwxfHxkYXJrJTIwZm9vdGJhbGwlMjBzdGFkaXVtJTIwbGlnaHRzJTIwYWJzdHJhY3R8ZW58MHx8fGdyZWVufDE3NzkyMDY5NTB8MA&ixlib=rb-4.1.0&q=85",
        "description": "Aerial football field at night; use as masked/blurred background behind login left panel.",
        "category": "background"
      }
    ],
    "texture_noise": [
      {
        "url": "https://images.pexels.com/photos/7641028/pexels-photo-7641028.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
        "description": "Dark mesh texture; use at 4–8% opacity as noise overlay on large containers.",
        "category": "texture"
      }
    ],
    "accent_visual": [
      {
        "url": "https://images.unsplash.com/photo-1631507366288-c8153653c9dd?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NDk1ODB8MHwxfHNlYXJjaHwxfHxnb2xkJTIwYWJzdHJhY3QlMjBsaWdodCUyMHN0cmVha3xlbnwwfHx8eWVsbG93fDE3NzkyMDY5NTZ8MA&ixlib=rb-4.1.0&q=85",
        "description": "Gold light streak; use as subtle blurred corner glow behind 'Alta' section header (very low opacity).",
        "category": "decorative"
      }
    ]
  },

  "instructions_to_main_agent": {
    "global": [
      "Remove default CRA centered header styles in App.css; do not center the app container.",
      "Set dark theme as default by adding class 'dark' to <html> or root wrapper.",
      "Implement bilingual toggle in header; store language in localStorage; all labels must have ES/EN strings.",
      "Use shadcn components from /app/frontend/src/components/ui (no raw HTML dropdown/calendar/toast).",
      "Use Sonner for toasts (already present).",
      "Every interactive and key informational element must include data-testid (kebab-case)."
    ],
    "dashboard": [
      "StickyFilterBar below header with chips + Select for leagues.",
      "Group picks by confidence; each group has count + collapse on mobile.",
      "MatchCard must show: confidence meter, market, odds (mono), motivation badges (both teams), freshness, anti-trap, risks, cash-out viability."
    ],
    "match_detail": [
      "Right rail: OddsComparisonTable + actions (mark won/lost/push).",
      "Main: 3-layer data sections with explicit missing/stale states.",
      "LineMovement component: arrow + delta + tooltip."
    ],
    "history_profile": [
      "Use Table for history; top KPI strip uses Cards.",
      "Add placeholder ROI but visually de-emphasize (muted + 'Próximamente')."
    ],
    "libraries": [
      "Install: framer-motion, recharts.",
      "Use lucide-react for icons (no emoji icons)."
    ],
    "tailwind_utilities": {
      "app_background": "bg-background text-foreground",
      "panel": "bg-card border border-border rounded-xl",
      "muted_text": "text-muted-foreground",
      "mono": "font-mono tabular-nums",
      "focus": "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    }
  },

  "general_ui_ux_design_guidelines_appendix": "- You must **not** apply universal transition. Eg: `transition: all`. This results in breaking transforms. Always add transitions for specific interactive elements like button, input excluding transforms\n- You must **not** center align the app container, ie do not add `.App { text-align: center; }` in the css file. This disrupts the human natural reading flow of text\n- NEVER: use AI assistant Emoji characters like`🤖🧠💭💡🔮🎯📚🎭🎬🎪🎉🎊🎁🎀🎂🍰🎈🎨🎰💰💵💳🏦💎🪙💸🤑📊📈📉💹🔢🏆🥇 etc for icons. Always use **FontAwesome cdn** or **lucid-react** library already installed in the package.json\n\n **GRADIENT RESTRICTION RULE**\nNEVER use dark/saturated gradient combos (e.g., purple/pink) on any UI element.  Prohibited gradients: blue-500 to purple 600, purple 500 to pink-500, green-500 to blue-500, red to pink etc\nNEVER use dark gradients for logo, testimonial, footer etc\nNEVER let gradients cover more than 20% of the viewport.\nNEVER apply gradients to text-heavy content or reading areas.\nNEVER use gradients on small UI elements (<100px width).\nNEVER stack multiple gradient layers in the same viewport.\n\n**ENFORCEMENT RULE:**\n    • Id gradient area exceeds 20% of viewport OR affects readability, **THEN** use solid colors\n\n**How and where to use:**\n   • Section backgrounds (not content backgrounds)\n   • Hero section header content. Eg: dark to light to dark color\n   • Decorative overlays and accent elements only\n   • Hero section with 2-3 mild color\n   • Gradients creation can be done for any angle say horizontal, vertical or diagonal\n\n- For AI chat, voice application, **do not use purple color. Use color like light green, ocean blue, peach orange etc**\n\n</Font Guidelines>\n\n- Every interaction needs micro-animations - hover states, transitions, parallax effects, and entrance animations. Static = dead. \n   \n- Use 2-3x more spacing than feels comfortable. Cramped designs look cheap.\n\n- Subtle grain textures, noise overlays, custom cursors, selection states, and loading animations: separates good from extraordinary.\n   \n- Before generating UI, infer the visual style from the problem statement (palette, contrast, mood, motion) and immediately instantiate it by setting global design tokens (primary, secondary/accent, background, foreground, ring, state colors), rather than relying on any library defaults. Don't make the background dark as a default step, always understand problem first and define colors accordingly\n    Eg: - if it implies playful/energetic, choose a colorful scheme\n           - if it implies monochrome/minimal, choose a black–white/neutral scheme\n\n**Component Reuse:**\n\t- Prioritize using pre-existing components from src/components/ui when applicable\n\t- Create new components that match the style and conventions of existing components when needed\n\t- Examine existing components to understand the project's component patterns before creating new ones\n\n**IMPORTANT**: Do not use HTML based component like dropdown, calendar, toast etc. You **MUST** always use `/app/frontend/src/components/ui/ ` only as a primary components as these are modern and stylish component\n\n**Best Practices:**\n\t- Use Shadcn/UI as the primary component library for consistency and accessibility\n\t- Import path: ./components/[component-name]\n\n**Export Conventions:**\n\t- Components MUST use named exports (export const ComponentName = ...)\n\t- Pages MUST use default exports (export default function PageName() {...})\n\n**Toasts:**\n  - Use `sonner` for toasts\"\n  - Sonner component are located in `/app/src/components/ui/sonner.tsx`\n\nUse 2–4 color gradients, subtle textures/noise overlays, or CSS-based noise to avoid flat visuals."
}
