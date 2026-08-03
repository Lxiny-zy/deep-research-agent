import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  ArrowDown,
  ArrowUpRight,
  BookOpenText,
  Bot,
  BrainCircuit,
  Braces,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  CircleDotDashed,
  CircleHelp,
  CircleX,
  Clock3,
  Command,
  Copy,
  Database,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  FileSearch,
  FileText,
  FlaskConical,
  Gauge,
  GitBranch,
  GitMerge,
  History,
  KeyRound,
  LibraryBig,
  ListFilter,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  Network,
  Orbit,
  PanelLeftClose,
  PanelLeftOpen,
  PanelTop,
  PencilLine,
  Play,
  Plus,
  Radar,
  RefreshCw,
  Route,
  Save,
  ScanSearch,
  Search,
  SearchCode,
  ServerCog,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  SquareStack,
  Target,
  Timer,
  Trash2,
  UserRoundCog,
  UsersRound,
  WandSparkles,
  Waypoints,
  Workflow,
  X,
  Zap,
  type LucideIcon,
  type LucideProps,
} from 'lucide-react'
import type { Behavior } from '../types'

const APP_ICONS = {
  activity: Activity,
  alert: AlertCircle,
  'arrow-left': ArrowLeft,
  'arrow-right': ArrowRight,
  'arrow-down': ArrowDown,
  'arrow-up-right': ArrowUpRight,
  book: BookOpenText,
  bot: Bot,
  brain: BrainCircuit,
  braces: Braces,
  check: Check,
  'check-circle': CheckCircle2,
  'chevron-down': ChevronDown,
  'chevron-right': ChevronRight,
  'circle-dashed': CircleDashed,
  'circle-dot-dashed': CircleDotDashed,
  help: CircleHelp,
  'circle-x': CircleX,
  clock: Clock3,
  command: Command,
  copy: Copy,
  database: Database,
  download: Download,
  external: ExternalLink,
  eye: Eye,
  'eye-off': EyeOff,
  'file-search': FileSearch,
  file: FileText,
  flask: FlaskConical,
  gauge: Gauge,
  branch: GitBranch,
  merge: GitMerge,
  history: History,
  key: KeyRound,
  library: LibraryBig,
  filter: ListFilter,
  loader: LoaderCircle,
  lock: LockKeyhole,
  logout: LogOut,
  menu: Menu,
  network: Network,
  orbit: Orbit,
  'panel-close': PanelLeftClose,
  'panel-open': PanelLeftOpen,
  panel: PanelTop,
  edit: PencilLine,
  play: Play,
  plus: Plus,
  radar: Radar,
  refresh: RefreshCw,
  route: Route,
  save: Save,
  'scan-search': ScanSearch,
  search: Search,
  'search-code': SearchCode,
  server: ServerCog,
  settings: Settings2,
  shield: ShieldCheck,
  'shield-alert': ShieldAlert,
  sliders: SlidersHorizontal,
  sparkles: Sparkles,
  stack: SquareStack,
  target: Target,
  timer: Timer,
  trash: Trash2,
  'user-cog': UserRoundCog,
  users: UsersRound,
  wand: WandSparkles,
  waypoints: Waypoints,
  workflow: Workflow,
  x: X,
  zap: Zap,
} satisfies Record<string, LucideIcon>

export type AppIconName = keyof typeof APP_ICONS

type AppIconProps = LucideProps & {
  name: AppIconName
}

export function AppIcon({ name, size = 18, strokeWidth = 1.8, className = '', ...props }: AppIconProps) {
  const Icon = APP_ICONS[name]
  return (
    <Icon
      size={size}
      strokeWidth={strokeWidth}
      className={`app-icon ${className}`.trim()}
      {...props}
    />
  )
}

const AGENT_ICON_BY_BEHAVIOR: Record<Behavior, AppIconName> = {
  plan: 'route',
  research: 'search-code',
  reflect: 'refresh',
  synthesize: 'file',
  critique: 'shield',
}

const AGENT_ICON_ALIASES: Record<string, AppIconName> = {
  bot: 'bot',
  brain: 'brain',
  route: 'route',
  plan: 'route',
  research: 'search-code',
  search: 'search-code',
  'search-code': 'search-code',
  reflect: 'refresh',
  refresh: 'refresh',
  synthesize: 'file',
  file: 'file',
  critique: 'shield',
  shield: 'shield',
  network: 'network',
  workflow: 'workflow',
}

export const AGENT_ICON_OPTIONS: { value: string; label: string; icon: AppIconName }[] = [
  { value: 'bot', label: '通用角色', icon: 'bot' },
  { value: 'route', label: '规划编排', icon: 'route' },
  { value: 'search', label: '研究检索', icon: 'search-code' },
  { value: 'reflect', label: '反思校验', icon: 'refresh' },
  { value: 'synthesize', label: '报告综合', icon: 'file' },
  { value: 'critique', label: '质量评审', icon: 'shield' },
  { value: 'network', label: '协作网络', icon: 'network' },
]

export function agentIconName(value?: string | null, behavior?: Behavior): AppIconName {
  const normalized = value?.trim().toLowerCase()
  if (normalized && AGENT_ICON_ALIASES[normalized]) return AGENT_ICON_ALIASES[normalized]
  return behavior ? AGENT_ICON_BY_BEHAVIOR[behavior] : 'bot'
}

export function AgentGlyph({
  icon,
  behavior,
  size = 20,
  className,
}: {
  icon?: string | null
  behavior?: Behavior
  size?: number
  className?: string
}) {
  return <AppIcon name={agentIconName(icon, behavior)} size={size} className={className} />
}
