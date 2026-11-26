import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { Splash } from '@/components/Splash'
import { AppShell } from '@/components/AppShell'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { SessionProvider } from '@/lib/session'
import { SignIn } from '@/pages/SignIn'
import { Overview } from '@/pages/Overview'
import { NotFound } from '@/pages/NotFound'
import { DevComponents } from '@/pages/DevComponents'
import { Enquiries } from '@/pages/Enquiries'
import { EnquiryNew } from '@/pages/EnquiryNew'
import { EnquiryDetail } from '@/pages/EnquiryDetail'
import { Applications } from '@/pages/Applications'
import { ApplicationDetail } from '@/pages/ApplicationDetail'
import { ReviewQueue } from '@/pages/ReviewQueue'
import { Disbursements } from '@/pages/Disbursements'
import { Policy } from '@/pages/Policy'
import { ReportDetail } from '@/pages/policy/ReportDetail'

export default function App() {
  return (
    <BrowserRouter>
      <Splash />
      <SessionProvider>
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
              <Route path="/dev/components" element={<DevComponents />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Route>
          <Route path="/" element={<Navigate to="/overview" replace />} />
        </Routes>
      </SessionProvider>
      <Toaster position="bottom-right" />
    </BrowserRouter>
  )
}
