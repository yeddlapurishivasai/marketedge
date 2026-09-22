import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { broadcastResponseToMainFrame } from '@azure/msal-browser/redirect-bridge'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthProvider'

if (window.self !== window.top) {
  void broadcastResponseToMainFrame()
} else {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <AuthProvider>
        <App />
      </AuthProvider>
    </StrictMode>,
  )
}
