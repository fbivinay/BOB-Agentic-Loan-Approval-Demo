import { createClient } from '@supabase/supabase-js'

// Used to sign in only. Every read and write goes through the backend API.
export const supabase = createClient(import.meta.env.VITE_SUPABASE_URL, import.meta.env.VITE_SUPABASE_ANON_KEY, {
  auth: { persistSession: true, autoRefreshToken: true },
})
