import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { DeviceStatusPage, KioskPage, KioskSettingsPage, WebPage } from './App.jsx'
import { createBrowserRouter, Link, Navigate, RouterProvider } from 'react-router-dom'

const router = createBrowserRouter([
  {
    path: '/', element: <App />, children: [
      { index: true, element: <Navigate to="/web" replace /> },
      { path: 'web', element: <WebPage /> },
      { path: 'kiosk', element: <KioskPage />, children: [
        { index: true, element: <></> },
        { path: 'settings', element: <KioskSettingsPage /> },
      ] },
      { path: 'admin/devices', element: <DeviceStatusPage /> },
      { path: '*', element: <div className="text-center py-12 space-y-4">
        <h2 className="text-xl font-bold">페이지를 찾을 수 없습니다</h2>
        <Link to="/web" className="text-indigo-300 underline">웹 포털로 이동</Link>
      </div> },
    ],
  },
])

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
