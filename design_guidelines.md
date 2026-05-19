{
  "design_system_name": "Decision Intelligence Terminal (Sports) — Dark Emerald/Cyan",
  "brand_attributes": [
    "professional",
    "disciplined",
    "explainable",
    "financial-terminal precise",
    "Apple-level clarity",
    "Stripe-like confidence visualization",
    "Bloomberg-like contextual density"
  ],
  "non_goals_and_forbidden": {
    "forbidden_visuals": [
      "flashy sportsbook / casino aesthetics",
      "aggressive high-risk cues (neon reds, flame icons, jackpot motifs)",
      "oversized confidence circles",
      "badge-on-badge stacking",
      "gradient text headings",
      "gradient buttons",
      "heatmap rainbow palettes",
      "purple as a primary accent"
    ],
    "gradient_restrictions": {
      "rule": "Gradients are decorative only and must not exceed 20% of viewport; never on text-heavy areas; never on small UI elements (<100px width); never stack multiple gradient layers.",
      "prohibited_examples": [
        "blue-500 to purple-600",
        "purple-500 to pink-500",
        "green-500 to blue-500",
        "red to pink"
      ],
      "enforcement": "If gradient area exceeds 20% of viewport OR impacts readability, fallback to solid colors."
    }
  },
  "inspiration_refs": {
    "visual_targets": [
      "Apple clarity (spacing, typography hierarchy, calm surfaces)",
      "Stripe risk/confidence patterns (scores + reasons + drill-down)",
      "Bloomberg terminal density tier (micro labels, mono numerals, aligned columns)",
      "Modern XAI UI (every score answers 'why' via tooltip/popover)"
    ],
    "web_refs": [
      {
        "title": "NN/g: Glassmorphism usability",
        "url": "https://www.nngroup.com/articles/glassmorphism/"
      },
      {
        "title": "Stripe: Fraud management guide (risk scores + drill-down patterns)",
        "url": "https://stripe.com/ae/guides/improve-fraud-management-with-radar-for-fraud-teams-and-stripe-data"
      },
      {
        "title": "Dribbble search: Bloomberg terminal",
        "url": "https://dribbble.com/search/bloomberg-terminal"
      }
    ]
  },
  "typography": {
    "fonts": {
      "heading": {
        "family": "Space Grotesk",
        "fallback": "ui-sans-serif, system-ui",
        "usage": "Page titles, section headers, narrative headings"
      },
      "body": {
        "family": "Inter",
        "fallback": "ui-sans-serif, system-ui",
        "usage": "Body copy, explanations, UI labels"
      },
      "mono": {
        "family": "IBM Plex Mono",
        "fallback": "ui-monospace, SFMono-Regular",
        "usage": "Scores, percentages, timestamps, deltas, IDs; use tabular numerals"
      }
    },
    "scale": {
      "h1": "text-4xl sm:text-5xl lg:text-6xl",
      "h2": "text-base md:text-lg",
      "body": "text-sm md:text-base",
      "micro_label": "text-[11px] uppercase tracking-[0.14em] text-muted-foreground",
      "mono_numeric": "text-[12px] md:text-[13px] font-mono-tabular"
    },
    "narrative_rules": [
      "Structure explanations as: Headline → 1-sentence reasoning → drivers list → tags → numeric evidence.",
      "Use micro-labels for context (e.g., 'VOLATILIDAD', 'FRAGILIDAD', 'MERCADOS').",
      "Numbers always in mono with tabular-nums; align decimals/percentages in columns.",
      "Tooltips must answer 'por qué' in ≤140 chars (Spanish canonical)."
    ]
  },
  "color_system": {
    "foundation": {
      "note": "Keep existing near-black + emerald/cyan accents. Evolve with semantic tiers; avoid replacing base tokens.",
      "existing_tokens_from_index_css": {
        "background": "hsl(220 26% 6%)",
        "card": "hsl(220 24% 9%)",
        "border": "hsl(220 16% 18%)",
        "primary_accent": "hsl(160 84% 45%) (emerald)",
        "accent": "hsl(200 92% 55%) (cyan)",
        "muted_foreground": "hsl(215 16% 70%)"
      }
    },
    "semantic_tokens_to_add": {
      "volatility": {
        "low": "hsl(160 84% 45%) with 12–18% alpha on dark surfaces",
        "medium": "hsl(45 96% 55%) with 12–18% alpha (amber warning, not gambling)",
        "high": "hsl(0 84% 60%) with 10–14% alpha (use sparingly; never neon)"
      },
      "fragility": {
        "stable": "emerald tint",
        "sensitive": "amber tint",
        "fragile": "muted rose tint (desaturated)"
      },
      "match_state": {
        "CONTROLLED_MATCH": {
          "label": "CONTROLADO",
          "icon": "ShieldCheck",
          "tone": "emerald"
        },
        "CHAOTIC_MATCH": {
          "label": "CAÓTICO",
          "icon": "Activity",
          "tone": "amber"
        },
        "HIGH_MOTIVATION": {
          "label": "ALTA MOTIVACIÓN",
          "icon": "Flame",
          "tone": "cyan"
        },
        "LOW_URGENCY": {
          "label": "BAJA URGENCIA",
          "icon": "Clock",
          "tone": "slate"
        }
      },
      "market_reco": {
        "best_for": "emerald",
        "avoid": "muted rose (not bright red)"
      }
    },
    "glassmorphism_rules": {
      "allowed_surfaces_only": [
        "modals",
        "dropdowns",
        "tooltips",
        "hover-cards",
        "drawers/sheets"
      ],
      "settings": {
        "blur": "backdrop-blur-[12px] to backdrop-blur-[18px]",
        "tint": "bg-white/[0.06] (≤8% white tint)",
        "border": "border-white/[0.08]",
        "shadow": "shadow-[0_18px_60px_rgba(0,0,0,0.55)]"
      },
      "not_allowed": [
        "base cards",
        "tables",
        "list rows"
      ]
    }
  },
  "design_tokens_css": {
    "instructions": "Main agent should extend /app/frontend/src/index.css :root with these tokens (do not remove existing).",
    "css": ":root {\n  /* Density */\n  --density-compact: 0.875; /* used as multiplier in component spacing */\n\n  /* Terminal micro-typography */\n  --label-tracking: 0.14em;\n\n  /* Semantic tiers (use with opacity on dark surfaces) */\n  --vol-low: 160 84% 45%;\n  --vol-med: 45 96% 55%;\n  --vol-high: 0 84% 60%;\n\n  --frag-stable: 160 84% 45%;\n  --frag-sensitive: 45 96% 55%;\n  --frag-fragile: 350 70% 62%; /* muted rose */\n\n  /* Tooltip/overlay glass */\n  --glass-bg: 0 0% 100%;\n  --glass-alpha: 0.06;\n  --glass-border-alpha: 0.08;\n}\n\n/* Utility: micro label */\n.micro-label {\n  font-size: 11px;\n  text-transform: uppercase;\n  letter-spacing: var(--label-tracking);\n  color: hsl(var(--muted-foreground));\n}\n\n/* Utility: overlay glass surface */\n.glass-surface {\n  background: rgba(255,255,255,var(--glass-alpha));\n  border: 1px solid rgba(255,255,255,var(--glass-border-alpha));\n  backdrop-filter: blur(14px);\n}\n"
  },
  "layout_and_grid": {
    "dashboard": {
      "pattern": "Breathable cards + terminal-density inner rows",
      "container": "max-w-[1280px] 2xl:max-w-[1440px] px-4 md:px-6",
      "grid": "grid grid-cols-1 lg:grid-cols-12 gap-4 md:gap-6",
      "zones": {
        "left_main": "lg:col-span-8",
        "right_context": "lg:col-span-4 (watchlist-like context: KPIs, engine notes, saved views)"
      }
    },
    "density_tier": {
      "when_to_use": [
        "tables",
        "filter bars",
        "pick rows",
        "history lists",
        "live page"
      ],
      "rules": [
        "Use micro-labels + mono numerals.",
        "Reduce vertical padding: py-2 instead of py-4.",
        "Prefer separators over heavy card borders.",
        "Keep dashboard cards breathable; density lives inside them."
      ]
    },
    "mobile_rules": {
      "filters": "Filter bar becomes horizontally-scrollable chip rail (ScrollArea) with snap-like spacing.",
      "intelligence_panel": "Stacks single column; timeline becomes accordion; radar chart collapses to summary stats below 640px.",
      "tables": "Use responsive table: key columns only + row expand (Collapsible) for details."
    }
  },
  "component_taxonomy": {
    "chips_and_tags": {
      "base_style": "text-[12px] leading-5 px-2.5 py-1 rounded-full border bg-secondary/40",
      "icon_size": "h-3.5 w-3.5",
      "driver_tags": [
        {
          "key": "form",
          "label_es": "Forma",
          "icon": "TrendingUp",
          "tone": "emerald"
        },
        {
          "key": "motivation",
          "label_es": "Motivación",
          "icon": "Target",
          "tone": "cyan"
        },
        {
          "key": "home-advantage",
          "label_es": "Localía",
          "icon": "Home",
          "tone": "slate"
        },
        {
          "key": "absences",
          "label_es": "Bajas",
          "icon": "UserMinus",
          "tone": "amber"
        },
        {
          "key": "fatigue",
          "label_es": "Fatiga",
          "icon": "BatteryWarning",
          "tone": "amber"
        },
        {
          "key": "rotation-risk",
          "label_es": "Rotación",
          "icon": "Shuffle",
          "tone": "muted-rose"
        }
      ],
      "engine_styles": [
        {
          "key": "conservative",
          "label_es": "Conservador",
          "icon": "Shield",
          "tone": "slate"
        },
        {
          "key": "protected-markets",
          "label_es": "Mercados Protegidos",
          "icon": "Lock",
          "tone": "emerald"
        },
        {
          "key": "low-fragility",
          "label_es": "Baja Fragilidad",
          "icon": "Layers",
          "tone": "cyan"
        },
        {
          "key": "value-hunting",
          "label_es": "Caza de Valor",
          "icon": "Search",
          "tone": "amber"
        },
        {
          "key": "live-momentum",
          "label_es": "Momentum en Vivo",
          "icon": "Zap",
          "tone": "cyan"
        }
      ]
    }
  },
  "components": {
    "component_path": {
      "shadcn_primary": "/app/frontend/src/components/ui/",
      "use_these": {
        "Card": "card.jsx",
        "Badge": "badge.jsx",
        "Button": "button.jsx",
        "Tooltip": "tooltip.jsx",
        "HoverCard": "hover-card.jsx",
        "Popover": "popover.jsx",
        "Tabs": "tabs.jsx",
        "Separator": "separator.jsx",
        "ScrollArea": "scroll-area.jsx",
        "Collapsible": "collapsible.jsx",
        "Accordion": "accordion.jsx",
        "Sheet": "sheet.jsx",
        "Drawer": "drawer.jsx",
        "Table": "table.jsx",
        "Progress": "progress.jsx",
        "Skeleton": "skeleton.jsx",
        "Sonner": "sonner.jsx"
      }
    },
    "ConfidenceIntelligenceCard": {
      "purpose": "Replace simple meter with explainable confidence + drivers + volatility/fragility + market guidance.",
      "backward_compat": {
        "required_prop": "score (existing)",
        "optional_props": [
          "drivers[]",
          "risk_score",
          "volatility_score",
          "fragility_score",
          "reasoning_summary",
          "match_state",
          "best_for[]",
          "avoid[]"
        ],
        "testid": "confidence-meter (must remain)"
      },
      "layout": {
        "desktop": "Card with 3 columns: (1) score + label, (2) drivers list, (3) risk/volatility/fragility + best/avoid",
        "mobile": "Stack: score → reasoning → drivers chips rail → risk/volatility/fragility → best/avoid"
      },
      "visual": {
        "score_viz": "Use Progress bar + numeric score (mono). Avoid giant circles. Add thin tick marks (CSS) for 0/25/50/75/100.",
        "confidence_label": "Map score to: Alta / Media / Baja (Spanish).",
        "driver_rows": "Each driver row: icon + label + contribution bar (tiny) + tooltip 'por qué'.",
        "states": "Use semantic tints for volatility/fragility; never rely on color alone—include label text."
      },
      "tooltips": {
        "pattern": "TooltipProvider + TooltipTrigger wrapping every numeric and chip.",
        "copy_limit": "≤140 chars",
        "example_es": "Volatilidad alta: señales contradictorias y rotación probable. Reduce stake o evita mercados sensibles."
      },
      "data_testids": {
        "score": "confidence-intelligence-score",
        "progress": "confidence-intelligence-progress",
        "drivers": "confidence-intelligence-drivers",
        "best_for": "confidence-intelligence-best-for",
        "avoid": "confidence-intelligence-avoid"
      }
    },
    "MotivationContextBlock": {
      "purpose": "Explain motivation with reasons, sources, urgency, and gameplay impact.",
      "layout": {
        "header": "Motivación (micro-label) + MotivationBadge summary",
        "body": "Two-column: reasons/sources on left; gameplay impact (+/-) on right",
        "mobile": "Accordion: Razones → Fuentes → Impacto"
      },
      "visual": {
        "badge": "Use Badge with icon + short label; avoid stacking multiple badges.",
        "impact_list": "Use + / − indicators with lucide icons (Plus, Minus) and muted semantic tints."
      },
      "data_testids": {
        "block": "motivation-context-block",
        "badge": "motivation-badge",
        "reasons": "motivation-reasons",
        "impact": "motivation-impact"
      }
    },
    "FilterIntelligenceBar": {
      "purpose": "Smart filters with dynamic counts, engine-style presets, saved views.",
      "layout": {
        "top_row": "Engine style preset chips (h-scroll on mobile)",
        "second_row": "Filter chips with counts + 'Saved views' button opens Sheet/Drawer",
        "right": "Compact summary: active filters count + reset"
      },
      "interaction": {
        "chips": "Use ToggleGroup for multi-select chips; each chip shows count in mono.",
        "saved_views": "Use Sheet (desktop) / Drawer (mobile) with glass surface.",
        "micro_motion": "Hover: border brighten + slight translate-y-[-1px]; Active: inset ring. No list-wide animations."
      },
      "mobile": {
        "pattern": "ScrollArea horizontal rail; keep touch targets min-h-10; show fade edges using mask-image."
      },
      "data_testids": {
        "bar": "picks-filter-bar",
        "engine_presets": "engine-style-presets",
        "saved_views_button": "saved-views-open-button",
        "reset": "filters-reset-button"
      }
    },
    "EmptyStateCoaching": {
      "purpose": "Replace 'no picks' with engine reasoning + bankroll discipline + suggested waiting strategy.",
      "layout": {
        "hero": "Short headline + 1 sentence: 'No apostar también es una decisión ganadora.'",
        "reasons": "Bulleted structured reasons (chips + explanation)",
        "tips": "Educational tips card + suggested filters/presets",
        "cta": "Primary: 'Ver mercados protegidos' / Secondary: 'Guardar vista'"
      },
      "tone": {
        "copy": "Calm, disciplined, non-judgmental. Avoid hype.",
        "visual": "Use Card + Separator; include subtle terminal-glow background only behind the empty state header (≤20% viewport)."
      },
      "data_testids": {
        "empty_state": "empty-state-no-value",
        "headline": "empty-state-headline",
        "reasons": "empty-state-reasons",
        "cta_primary": "empty-state-primary-cta"
      }
    },
    "MatchIntelligencePanel": {
      "purpose": "Terminal-style enriched match detail: narrative drivers timeline + volatility radar + key signals + best/avoid + fragility breakdown.",
      "layout": {
        "desktop": "Two-column: left narrative timeline; right analytics stack (radar + best/avoid + fragility)",
        "mobile": "Tabs: Resumen / Señales / Mercados / Riesgo"
      },
      "charts": {
        "library": "Recharts (already installed)",
        "radar": "RadarChart for drivers strength + volatility; keep strokes thin; fill opacity 0.12; use emerald/cyan only.",
        "fallback_under_640": "Replace radar with 3 summary stats rows (Volatilidad, Fragilidad, Riesgo) + top 3 drivers."
      },
      "timeline": {
        "pattern": "Vertical list with timestamps/labels in mono; each event has a tooltip 'por qué'.",
        "interaction": "Collapsible per driver for evidence bullets."
      },
      "data_testids": {
        "panel": "match-intelligence-panel",
        "radar": "match-intelligence-radar",
        "best_avoid": "match-intelligence-best-avoid",
        "timeline": "match-intelligence-timeline"
      }
    },
    "PickCard_or_PickRow_Evolution": {
      "goal": "Surface drivers + tags inline without visual noise.",
      "pattern": {
        "row": "Left: teams + market; Middle: driver chips (max 3 visible + '+N'); Right: confidence score + volatility label",
        "expand": "Row expands (Collapsible) to show reasoning summary + best/avoid + fragility breakdown"
      },
      "density": "Use micro-labels and mono numerals; keep row height compact (min-h-14).",
      "data_testids": {
        "row": "pick-row",
        "expand_button": "pick-row-expand-button"
      }
    }
  },
  "motion_and_micro_interactions": {
    "principles": [
      "Use transform/opacity only; avoid animating large lists.",
      "No transition: all. Only transition border-color, background-color, box-shadow, color.",
      "Duration 120–220ms; never >300ms.",
      "Prefer subtle hover lift (translateY -1px) on cards/chips; press scale 0.98 on buttons."
    ],
    "framer_motion_usage": {
      "where": [
        "modal entrances",
        "drawer/sheet",
        "single card reveal",
        "tooltip/hover-card subtle fade"
      ],
      "avoid": [
        "animating entire pick lists",
        "continuous looping animations except live pulse dot"
      ]
    }
  },
  "accessibility": {
    "requirements": [
      "Every numeric score and every chip/tag must have a tooltip explaining meaning (≤140 chars).",
      "Color is not the only signal: include icon + label for states.",
      "Focus-visible ring must remain (already defined in index.css).",
      "Touch targets: min-h-10 for chips/buttons on mobile.",
      "Respect prefers-reduced-motion (already used for live pulse)."
    ]
  },
  "images_and_textures": {
    "image_urls": [
      {
        "category": "background_texture",
        "description": "Subtle noise/grain overlay (CSS preferred). No large photos; keep terminal feel.",
        "url": "(use CSS noise; no external image required)"
      }
    ],
    "css_noise_snippet": "/* Add to a top-level container (e.g., DashboardPage wrapper) */\n.noise-overlay {\n  position: relative;\n}\n.noise-overlay:before {\n  content: '';\n  pointer-events: none;\n  position: absolute;\n  inset: 0;\n  opacity: 0.06;\n  mix-blend-mode: overlay;\n  background-image: url('data:image/svg+xml;utf8,<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"120\" height=\"120\"><filter id=\"n\"><feTurbulence type=\"fractalNoise\" baseFrequency=\"0.9\" numOctaves=\"3\" stitchTiles=\"stitch\"/></filter><rect width=\"120\" height=\"120\" filter=\"url(%23n)\" opacity=\"0.35\"/></svg>');\n}\n"
  },
  "implementation_notes_js": {
    "note": "Project uses .js (not .tsx). Keep components in JS, named exports for components, default exports for pages.",
    "tooltip_scaffold": "// Example pattern (JS)\nimport { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../components/ui/tooltip';\n\nexport const ExplainableMetric = ({ label, value, why, testId }) => (\n  <div className=\"flex items-center justify-between gap-3\" data-testid={testId}>\n    <span className=\"micro-label\">{label}</span>\n    <TooltipProvider delayDuration={120}>\n      <Tooltip>\n        <TooltipTrigger asChild>\n          <span className=\"font-mono-tabular text-[13px] text-foreground\">{value}</span>\n        </TooltipTrigger>\n        <TooltipContent className=\"glass-surface text-xs max-w-[260px]\">{why}</TooltipContent>\n      </Tooltip>\n    </TooltipProvider>\n  </div>\n);\n"
  },
  "instructions_to_main_agent": [
    "Preserve existing dark theme tokens; only extend with semantic tokens for volatility/fragility/match_state.",
    "Implement the 5 artifacts using shadcn components listed; overlays (tooltips/sheets/dialogs) use glass-surface only.",
    "Ensure every interactive and key informational element has data-testid (kebab-case). Preserve existing testids like data-testid=\"confidence-meter\".",
    "ConfidenceIntelligenceCard must accept existing score prop and gracefully degrade when optional fields missing.",
    "Filter bar on mobile must be horizontal ScrollArea chip rail; counts in mono.",
    "Match detail: use Recharts RadarChart on desktop; collapse to summary stats under 640px.",
    "No gradient buttons; no gradient text; keep gradients only as subtle background glow (≤20% viewport).",
    "Avoid animating large lists; keep transitions specific (border-color, background-color, box-shadow, color)."
  ],
  "General UI UX Design Guidelines": "- You must **not** apply universal transition. Eg: `transition: all`. This results in breaking transforms. Always add transitions for specific interactive elements like button, input excluding transforms\n    - You must **not** center align the app container, ie do not add `.App { text-align: center; }` in the css file. This disrupts the human natural reading flow of text\n   - NEVER: use AI assistant Emoji characters like`🤖🧠💭💡🔮🎯📚🎭🎬🎪🎉🎊🎁🎀🎂🍰🎈🎨🎰💰💵💳🏦💎🪙💸🤑📊📈📉💹🔢🏆🥇 etc for icons. Always use **FontAwesome cdn** or **lucid-react** library already installed in the package.json\n\n **GRADIENT RESTRICTION RULE**\nNEVER use dark/saturated gradient combos (e.g., purple/pink) on any UI element.  Prohibited gradients: blue-500 to purple 600, purple 500 to pink-500, green-500 to blue-500, red to pink etc\nNEVER use dark gradients for logo, testimonial, footer etc\nNEVER let gradients cover more than 20% of the viewport.\nNEVER apply gradients to text-heavy content or reading areas.\nNEVER use gradients on small UI elements (<100px width).\nNEVER stack multiple gradient layers in the same viewport.\n\n**ENFORCEMENT RULE:**\n    • Id gradient area exceeds 20% of viewport OR affects readability, **THEN** use solid colors\n\n**How and where to use:**\n   • Section backgrounds (not content backgrounds)\n   • Hero section header content. Eg: dark to light to dark color\n   • Decorative overlays and accent elements only\n   • Hero section with 2-3 mild color\n   • Gradients creation can be done for any angle say horizontal, vertical or diagonal\n\n- For AI chat, voice application, **do not use purple color. Use color like light green, ocean blue, peach orange etc**\n\n</Font Guidelines>\n\n- Every interaction needs micro-animations - hover states, transitions, parallax effects, and entrance animations. Static = dead. \n   \n- Use 2-3x more spacing than feels comfortable. Cramped designs look cheap.\n\n- Subtle grain textures, noise overlays, custom cursors, selection states, and loading animations: separates good from extraordinary.\n   \n- Before generating UI, infer the visual style from the problem statement (palette, contrast, mood, motion) and immediately instantiate it by setting global design tokens (primary, secondary/accent, background, foreground, ring, state colors), rather than relying on any library defaults. Don't make the background dark as a default step, always understand problem first and define colors accordingly\n    Eg: - if it implies playful/energetic, choose a colorful scheme\n           - if it implies monochrome/minimal, choose a black–white/neutral scheme\n\n**Component Reuse:**\n\t- Prioritize using pre-existing components from src/components/ui when applicable\n\t- Create new components that match the style and conventions of existing components when needed\n\t- Examine existing components to understand the project's component patterns before creating new ones\n\n**IMPORTANT**: Do not use HTML based component like dropdown, calendar, toast etc. You **MUST** always use `/app/frontend/src/components/ui/ ` only as a primary components as these are modern and stylish component\n\n**Best Practices:**\n\t- Use Shadcn/UI as the primary component library for consistency and accessibility\n\t- Import path: ./components/[component-name]\n\n**Export Conventions:**\n\t- Components MUST use named exports (export const ComponentName = ...)\n\t- Pages MUST use default exports (export default function PageName() {...})\n\n**Toasts:**\n  - Use `sonner` for toasts\"\n  - Sonner component are located in `/app/src/components/ui/sonner.tsx`\n\nUse 2–4 color gradients, subtle textures/noise overlays, or CSS-based noise to avoid flat visuals."
}
