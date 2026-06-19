import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import App from './App'
import './index.css'
import AgentSquarePage from './pages/AgentSquarePage'
import HistoryPage from './pages/HistoryPage'
import NewResearchPage from './pages/NewResearchPage'
import RunPage from './pages/RunPage'
import SettingsPage from './pages/SettingsPage'
import WorkflowBuilderPage from './pages/WorkflowBuilderPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1 } },
})

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <NewResearchPage /> },
      { path: 'runs/:id', element: <RunPage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'workflows', element: <WorkflowBuilderPage /> },
      { path: 'agents', element: <AgentSquarePage /> },
      { path: 'settings', element: <SettingsPage /> },
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
