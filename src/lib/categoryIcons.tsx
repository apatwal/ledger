// Maps a category name → a lucide icon component, so the ledger can show a small
// glyph beside each category. Matching is case-insensitive with a few aliases;
// anything unknown falls back to a neutral circle.
import {
  Utensils,
  ShoppingBag,
  Car,
  Plane,
  Receipt,
  Film,
  HeartPulse,
  TrendingUp,
  Banknote,
  ArrowLeftRight,
  CreditCard,
  Percent,
  Home,
  GraduationCap,
  Gift,
  Dog,
  Dumbbell,
  Wifi,
  Fuel,
  Coffee,
  Circle,
  type LucideIcon,
} from 'lucide-react'

// Keys are compared lowercased. Order-independent — first exact match wins,
// otherwise a substring match, otherwise the default.
const ICONS: Record<string, LucideIcon> = {
  'food & drink': Utensils,
  'food and drink': Utensils,
  food: Utensils,
  dining: Utensils,
  restaurants: Utensils,
  groceries: ShoppingBag,
  coffee: Coffee,
  shopping: ShoppingBag,
  transportation: Car,
  transport: Car,
  travel: Plane,
  gas: Fuel,
  fuel: Fuel,
  'bills & utilities': Receipt,
  'bills and utilities': Receipt,
  bills: Receipt,
  utilities: Receipt,
  internet: Wifi,
  entertainment: Film,
  health: HeartPulse,
  'health & fitness': HeartPulse,
  medical: HeartPulse,
  fitness: Dumbbell,
  investment: TrendingUp,
  investments: TrendingUp,
  income: Banknote,
  payroll: Banknote,
  transfer: ArrowLeftRight,
  transfers: ArrowLeftRight,
  'payments & credits': CreditCard,
  payment: CreditCard,
  payments: CreditCard,
  credit: CreditCard,
  fees: Percent,
  fee: Percent,
  'bank fees': Percent,
  interest: Percent,
  rent: Home,
  mortgage: Home,
  housing: Home,
  home: Home,
  education: GraduationCap,
  gifts: Gift,
  'gifts & donations': Gift,
  donations: Gift,
  pets: Dog,
  uncategorized: Circle,
}

export function iconForCategory(category: string | null | undefined): LucideIcon {
  if (!category) return Circle
  const key = category.trim().toLowerCase()
  if (ICONS[key]) return ICONS[key]
  // Substring fallback — e.g. "Food & Drink · Fast Food" → Utensils.
  for (const [name, Icon] of Object.entries(ICONS)) {
    if (key.includes(name) || name.includes(key)) return Icon
  }
  return Circle
}
