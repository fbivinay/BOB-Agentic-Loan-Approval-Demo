import { lazy, Suspense, type ComponentType } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { Splash } from '@/components/Splash'
import { AppShell, PageSkeleton } from '@/components/AppShell'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { SessionProvider } from '@/lib/session'
import { SignIn } from '@/pages/SignIn'
import { NotFound } from '@/pages/NotFound'

// Each page loads when first opened, so sign-in stays small and charts (Recharts) load only on Analytics.
const page = <K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) => lazy(() => load().then((m) => ({ default: m[name] })))

const Overview = page(() => import('@/pages/Overview'), 'Overview')
const Enquiries = page(() => import('@/pages/Enquiries'), 'Enquiries')
const EnquiryNew = page(() => import('@/pages/EnquiryNew'), 'EnquiryNew')
const EnquiryDetail = page(() => import('@/pages/EnquiryDetail'), 'EnquiryDetail')
const Applications = page(() => import('@/pages/Applications'), 'Applications')
const ApplicationDetail = page(() => import('@/pages/ApplicationDetail'), 'ApplicationDetail')
const ReviewQueue = page(() => import('@/pages/ReviewQueue'), 'ReviewQueue')
const Disbursements = page(() => import('@/pages/Disbursements'), 'Disbursements')
const Policy = page(() => import('@/pages/Policy'), 'Policy')
const ReportDetail = page(() => import('@/pages/policy/ReportDetail'), 'ReportDetail')
const Analytics = page(() => import('@/pages/Analytics'), 'Analytics')
const System = page(() => import('@/pages/System'), 'System')
const Settings = page(() => import('@/pages/Settings'), 'Settings')

export default function App() {
  return (
    <BrowserRouter>
      <Splash />
      <SessionProvider>
        <Suspense fallback={<PageSkeleton />}>
          <Routes>
            <Route path="/sign-in" element={<SignIn />} />
            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                <Route path="/overview" element={<Overview />} />
                <Route path="/enquiries" element={<Enquiries />} />
                <Route path="/enquiries/new" element={<EnquiryNew />} />
                <Route path="/enquiries/:id" element={<EnquiryDetail />} />
                <Route path="/applications" element={<Applications />} />
                <Route path="/applications/:id/:tab?" element={<ApplicationDetail />} />
                <Route path="/review" element={<ReviewQueue />} />
                <Route path="/disbursements" element={<Disbursements />} />
                <Route path="/policy/reports/:id" element={<ReportDetail />} />
                <Route path="/policy/:tab?" element={<Policy />} />
                <Route path="/analytics" element={<Analytics />} />
                <Route path="/system" element={<System />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<NotFound />} />
              </Route>
            </Route>
            <Route path="/" element={<Navigate to="/overview" replace />} />
          </Routes>
        </Suspense>
      </SessionProvider>
      <Toaster position="bottom-right" />
    </BrowserRouter>
  )
}
