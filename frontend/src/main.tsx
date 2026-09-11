import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import App from './App'
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
        path: 'history',
        lazy: async () => ({ Component: (await import('./pages/HistoryPage')).default }),
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
