import { BarChart3, ClipboardList, FileStack, Gavel, IdCard, LayoutDashboard, Activity, Landmark, ScrollText, type LucideIcon } from 'lucide-react'

export type NavItem = { to: string; label: string; icon: LucideIcon; any: string[] }

// In the order a loan moves: what comes in, what is decided, what governs it, what is paid out, then the oversight views.
// `any`: shown when the person holds at least one of these permissions; empty means everyone.
export const NAV: NavItem[] = [
  { to: '/overview', label: 'Overview', icon: LayoutDashboard, any: [] },
  { to: '/enquiries', label: 'Enquiries', icon: ClipboardList, any: ['enquiry.manage', 'enquiry.view'] },
  { to: '/applications', label: 'Applications', icon: FileStack, any: ['application.view'] },
  { to: '/kyc', label: 'KYC records', icon: IdCard, any: ['application.view'] },
  { to: '/review', label: 'Review queue', icon: Gavel, any: ['decision.make'] },
  { to: '/policy', label: 'Policy', icon: ScrollText, any: ['policy.view'] },
  { to: '/disbursements', label: 'Disbursements', icon: Landmark, any: ['disbursement.view'] },
  { to: '/analytics', label: 'Analytics', icon: BarChart3, any: ['analytics.view'] },
  { to: '/system', label: 'System status', icon: Activity, any: ['system.view'] },
]

export const navFor = (perms: string[]) => NAV.filter((n) => n.any.length === 0 || n.any.some((p) => perms.includes(p)))
