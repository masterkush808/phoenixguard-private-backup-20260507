import {
  ChartCandlestick,
  Clock3,
  CloudDownload,
  Gauge,
  Infinity as InfinityIcon,
  MonitorUp,
  Radar,
  ShieldCheck,
  Sparkles,
  SlidersHorizontal
} from "lucide-react";

export const backgroundImages = [
  "/css-control/landing-transition-market-vision.png",
  "/css-control/landing-transition-market-vision-alt.png",
  "/css-control/landing-transition-lifestyle-suite.png",
  "/css-control/landing-transition-lifestyle-travel.png"
];

export const publicNav = [
  { href: "/", label: "Home" },
  { href: "/pricing", label: "Plans" },
  { href: "/risk-disclosure", label: "Disclosure" },
  { href: "/login", label: "Login" }
];

export const publicExploreLinks = [
  { href: "/pricing", label: "Access Plans", copy: "Free preview, focused daily access, or continuous professional availability." },
  { href: "/risk-disclosure", label: "Risk Disclosure", copy: "Read the market, broker, automation, and jurisdiction risk notice." },
  { href: "/login", label: "Client Portal", copy: "Create a verified account and complete the protected setup path." }
];

export const heroSlides = [
  {
    eyebrow: "Origin",
    title: "Founded from live-market discipline.",
    copy: "The 808 vision began in 2020: a focused effort to turn years of financial-market observation into a refined SaaS experience for serious market tracking.",
    detail: "Founded 2020"
  },
  {
    eyebrow: "Founder",
    title: "Developed by Thabang J. Masoabi.",
    copy: "Built under the direction of an AI and machine-learning computer science specialist, financial-market trading analyst, and forex trader with 10 years of market experience.",
    detail: "AI, ML, and market-analysis leadership"
  },
  {
    eyebrow: "Hybrid Intelligence",
    title: "A premium analytical system with in-house intelligence.",
    copy: "The Hybrid name reflects a disciplined blend of advanced AI techniques, calibrated machine-learning models, and structured operator controls designed for clear market awareness.",
    detail: "In-house model intelligence"
  },
  {
    eyebrow: "Mission",
    title: "Market clarity without careless access.",
    copy: "808Fx is built to help verified clients monitor market conditions, manage their operating window, and approach automated workflows with measured responsibility.",
    detail: "Financial-market tracking SaaS"
  },
  {
    eyebrow: "Vision",
    title: "Patent-pending architecture for the next operating class.",
    copy: "The system is positioned for a premium client base that values polished onboarding, controlled access, risk transparency, and dependable intelligence before any live connection is considered.",
    detail: "Patent pending"
  }
];

export const portalNav = [
  { href: "/app", label: "Command", icon: MonitorUp },
  { href: "/app/tracker", label: "Tracker", icon: Radar },
  { href: "/app/downloads", label: "Downloads", icon: CloudDownload },
  { href: "/admin", label: "Admin", icon: SlidersHorizontal }
];

export const hybridGates = [
  {
    label: "Identity",
    title: "Verified client entry",
    copy: "Each client starts with confirmed account ownership, clean contact details, and a private portal session before paid services or protected tools become available.",
    icon: ChartCandlestick
  },
  {
    label: "Access",
    title: "Clear plan value",
    copy: "Choose the operating window that fits your level of commitment: a free preview, a focused daily package, or continuous professional availability.",
    icon: ShieldCheck
  },
  {
    label: "Readiness",
    title: "Prepared before operation",
    copy: "Risk acknowledgement, broker compatibility, device readiness, and current system state are kept aligned before the premium workspace opens.",
    icon: CloudDownload
  }
];

export const planCards = [
  {
    code: "hybrid-free-2h",
    name: "Free Preview",
    price: "$0",
    cadence: "starter access",
    runtime: "2 hours daily uptime",
    icon: Sparkles,
    bestFor: "New clients who want to experience the portal and setup flow before choosing a paid operating window.",
    includes: [
      "Verified account and email-first onboarding",
      "Two hours of daily preview uptime access",
      "Risk disclosure acknowledgement before protected tools open",
      "Upgrade path into Standard or Professional access"
    ]
  },
  {
    code: "hybrid-standard-6h",
    name: "Standard Access",
    price: "$20",
    cadence: "per month",
    runtime: "Up to 6 hours per day",
    icon: Clock3,
    bestFor: "Disciplined part-time operators who want a serious daily window without paying for around-the-clock availability.",
    includes: [
      "Monthly verified client license",
      "Daily runtime window designed around a six-hour operating limit",
      "Plan state, disclosure record, broker binding, and device readiness held together",
      "Freshness controls prepared for stale market, account, and connector state"
    ]
  },
  {
    code: "hybrid-professional-24x7",
    name: "Professional Access",
    price: "$100",
    cadence: "per month",
    runtime: "24/7 eligible runtime",
    icon: InfinityIcon,
    bestFor: "Full-time operators who need continuous eligibility and a stronger operating posture.",
    includes: [
      "Professional verified client license",
      "Continuous runtime eligibility while payment, disclosure, and device health remain valid",
      "Priority readiness controls for market tracking, broker connection, and session continuity",
      "Designed for live deployment with stronger monitoring and stale-data protection"
    ]
  },
  {
    code: "scale-review",
    name: "Scale Review",
    price: "Review",
    cadence: "before rollout",
    runtime: "Custom controls",
    icon: Gauge,
    bestFor: "Teams, larger account structures, or regulated environments that need review before wider deployment.",
    includes: [
      "Jurisdiction, broker, and automation-policy review before expansion",
      "Custom runtime, access, support, and monitoring requirements",
      "Production secrets, domain, email, billing, and storage readiness review",
      "Release-control and operational escalation planning"
    ]
  }
];

export const pricingRows = [
  {
    item: "Monthly access",
    free: "$0 preview",
    standard: "$20 per month",
    professional: "$100 per month",
    scale: "Reviewed before activation"
  },
  {
    item: "Runtime eligibility",
    free: "2 hours daily uptime",
    standard: "Up to 6 hours per day",
    professional: "24/7 eligible runtime",
    scale: "Custom runtime controls"
  },
  {
    item: "Client setup",
    free: "Verified email and disclosure required",
    standard: "Verified email, disclosure, payment, broker, and device required",
    professional: "Verified email, disclosure, payment, broker, and device required",
    scale: "Expanded controls by review"
  },
  {
    item: "Freshness posture",
    free: "Preview readiness checks",
    standard: "Short validity windows and device freshness checks",
    professional: "Short validity windows, device freshness, and stronger monitoring posture",
    scale: "Monitoring policy review"
  },
  {
    item: "Workspace access",
    free: "Preview access only after setup passes",
    standard: "Locked until setup passes",
    professional: "Locked until setup passes",
    scale: "Versioned release control"
  }
];

export const releaseRows = [
  {
    version: "locked",
    channel: "Account setup",
    artifact: "Connector package appears after approval",
    hash: "Held until access is complete",
    state: "Locked"
  },
  {
    version: "2026.06",
    channel: "Tracker",
    artifact: "Tracker workspace access",
    hash: "Confirmed during release",
    state: "Guarded"
  },
  {
    version: "2026.06",
    channel: "Compliance",
    artifact: "Risk disclosure acknowledgement",
    hash: "Recorded before download",
    state: "Required"
  }
];

export const disclosurePoints = [
  {
    title: "Software only; no financial advice",
    copy: "The 808Fx Standard Hybrid System powered by the PhoenixGuard Engine is a market-tracking, workflow, analytics, and access-control SaaS product. It does not provide financial advice, investment advice, trading advice, tax advice, legal advice, brokerage advice, portfolio management, or personalized recommendations. No signal, alert, model output, dashboard, support message, onboarding step, plan description, or system status should be treated as an instruction to buy, sell, hold, trade, deposit, withdraw, increase risk, reduce risk, or connect any account."
  },
  {
    title: "High-risk leveraged markets",
    copy: "Forex, CFDs, futures, commodities, swaps, crypto derivatives, digital options, margin products, and other leveraged instruments are high-risk products and may not be suitable for all users. Leverage can amplify losses quickly. Market gaps, volatility, spreads, commissions, swaps, financing charges, slippage, liquidity changes, news events, rejected orders, margin calls, forced liquidation, and broker-side execution rules can materially affect outcomes. You can lose part or all of the funds placed at risk, and where protections do not apply, losses may exceed the amount deposited."
  },
  {
    title: "No profit, accuracy, loss-limit, or uptime guarantee",
    copy: "No website text, plan, tracker state, analytical output, alert, model score, confidence level, automation status, historical result, demonstration, screenshot, testimonial, or support response is a promise of profit, accuracy, uninterrupted operation, loss prevention, favorable execution, suitability, or future performance. The system can be wrong, delayed, unavailable, incomplete, stale, misconfigured, interrupted, or unsuitable for live use. Past, simulated, hypothetical, paper, backtested, or observed performance is not necessarily indicative of future results."
  },
  {
    title: "AI and machine-learning limitations",
    copy: "AI and machine-learning outputs are probabilistic and can be wrong, stale, incomplete, biased, overfit, misread changing conditions, or fail during market regime shifts. No AI system can predict future market movement, sudden news, liquidity events, broker behavior, spreads, execution quality, or account-specific outcomes. You must independently review all information and decide whether any action is lawful, suitable, and acceptable for your own situation."
  },
  {
    title: "Automation, stale data, and latency risk",
    copy: "Automated and semi-automated workflows can act faster than a person can review every condition. Market data, broker data, account sync, prices, charts, model states, alerts, and commands can freeze, arrive late, repeat, become stale, be interrupted, or be unavailable. Devices can disconnect, networks can fail, brokers can reject or delay orders, and market conditions can change before any action reaches an account. You are responsible for monitoring the system, confirming live account state directly with your broker, and stopping use when conditions are unclear."
  },
  {
    title: "Broker, platform, and OTC counterparty risk",
    copy: "Your broker or platform controls account opening, margin, prices, quotes, spreads, swaps, markups, execution, order rejection, liquidation, withdrawals, statements, tax records, account protections, and customer support under its own agreements. OTC forex and CFD products may not be traded on an exchange; the dealer or issuer may be your counterparty, may set or influence prices, and may show prices that differ from exchange, interbank, or other market prices. Broker outages, permissions, limits, restrictions, or withdrawal issues can cause loss or prevent action."
  },
  {
    title: "Jurisdiction and broker permission responsibility",
    copy: "You must use the system only with brokers, account types, instruments, countries, regions, exchanges, and products where your use of automated or assisted trading is lawful and permitted by the broker's terms. You are responsible for verifying local law, tax obligations, platform rules, broker policies, leverage limits, automated-trading restrictions, registration obligations, and any required permissions before connecting an account. Do not use the system where forex, CFDs, leverage, automation, or related products are restricted or prohibited."
  },
  {
    title: "Account connection is your decision",
    copy: "If you connect a live or demo trading account, you do so voluntarily and at your own risk. You remain responsible for the broker you choose, the account you connect, the capital you deposit, the settings you enable, the operating window you use, the position size or stake you choose, the times you operate, and every trading outcome that follows. The system is not a broker, exchange, custodian, dealer, counterparty, bank, futures commission merchant, retail foreign exchange dealer, introducing broker, commodity trading advisor, commodity pool operator, or investment adviser unless expressly stated in a signed written agreement."
  },
  {
    title: "Security and credential handling",
    copy: "The portal is designed not to collect broker passwords. Do not send broker passwords, investor passwords, seed phrases, private keys, banking credentials, card details, one-time codes, account recovery secrets, or payment details through support messages, screenshots, forms, or any field that is not explicitly provided by a regulated payment processor or authorized broker flow. You are responsible for protecting your own devices, accounts, credentials, broker access, and network security."
  },
  {
    title: "Testing before live use",
    copy: "Before any live use, test the setup in a non-live, demo, simulation, paper, or limited-risk environment. Confirm broker rules, account binding, device identity, release version, market-data freshness, command expiry, runtime limits, permissions, risk controls, and your own ability to supervise the account before relying on the system with real funds."
  },
  {
    title: "Access restriction and revocation",
    copy: "Access may remain locked, paused, limited, revoked, refused, or reviewed when registration, email confirmation, payment, subscription status, license status, disclosure acceptance, broker binding, device heartbeat, release requirements, jurisdiction concerns, abuse signals, chargebacks, stale-data checks, security checks, policy conflicts, or operational safety checks are incomplete or unsafe."
  },
  {
    title: "Fees, conflicts, and third parties",
    copy: "Subscription fees, broker fees, spreads, commissions, swaps, financing costs, data costs, platform costs, withdrawal fees, taxes, and other charges can reduce returns or increase losses. Any broker relationship, referral arrangement, affiliate compensation, sponsored material, paid testimonial, data-provider limitation, or platform preference should be evaluated carefully before use. You should not rely on promotional material that minimizes risk or suggests guaranteed outcomes."
  },
  {
    title: "Independent review",
    copy: "You should seek independent financial, legal, tax, regulatory, and technical advice before using this system. This disclosure is written for operational protection and user awareness, but it is not a substitute for advice from a qualified professional familiar with your country, broker, account, instruments, tax position, risk tolerance, and trading activity."
  },
  {
    title: "Your acknowledgement",
    copy: "By continuing, creating an account, activating a free preview, paying for access, connecting a broker account, downloading a release, opening the tracker, or using any PhoenixGuard-powered service, you confirm that you have read this disclosure, understand the risks, accept full responsibility for your decisions and outcomes, and agree that the system is provided as an operational SaaS tool without any guarantee of profit, accuracy, suitability, legal availability in your location, favorable execution, uninterrupted access, or protection from loss."
  }
];
