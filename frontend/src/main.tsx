import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import App from './App'
import RouteFallback from './components/RouteFallback'
// 品牌展示字（自托管，符合 CSP 的 font-src 'self'）
import '@fontsource/playfair-display/400.css'
import '@fontsource/playfair-display/400-italic.css'
import '@fontsource/playfair-display/600.css'
import './styles/index.css'
import ErrorPage, { NotFoundPage } from './pages/ErrorPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1 } },
})

const router = createBrowserRouter([
  ...(import.meta.env.DEV
    ? [
        {
          path: '/preview/live-telemetry',
          lazy: async () => ({
            Component: (await import('./pages/LiveTelemetryPreviewPage')).default,
          }),
        },
      ]
    : []),
  {
    path: '/',
    element: <App />,
    errorElement: <ErrorPage />,
    hydrateFallbackElement: <RouteFallback />,
    children: [
      {
        index: true,
        lazy: async () => ({ Component: (await import('./pages/NewResearchPage')).default }),
      },
      { path: 'welcome', element: null },
      {
        path: 'runs/:id',
        lazy: async () => ({ Component: (await import('./pages/RunPage')).default }),
      },
      {
        path: 'runs/:id/read',
        lazy: async () => ({ Component: (await import('./pages/ReaderPage')).default }),
      },
      {
        path: 'history',
        lazy: async () => ({ Component: (await import('./pages/HistoryPage')).default }),
      },
      {
        path: 'qa/:id?',
        lazy: async () => ({ Component: (await import('./pages/QaPage')).default }),
      },
      {
        path: 'library',
        lazy: async () => ({ Component: (await import('./pages/LibraryPage')).default }),
      },
      {
        path: 'workflows',
        lazy: async () => ({ Component: (await import('./pages/WorkflowBuilderPage')).default }),
      },
      {
        path: 'agents',
        lazy: async () => ({ Component: (await import('./pages/AgentSquarePage')).default }),
      },
      {
        path: 'settings',
        lazy: async () => ({ Component: (await import('./pages/SettingsPage')).default }),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
