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
