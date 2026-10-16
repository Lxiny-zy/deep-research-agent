import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, it } from 'vitest'
import { useQaDraft } from './useQaDraft'

beforeEach(() => {
  sessionStorage.clear()
  window.dispatchEvent(new Event('dr:credentials-cleared'))
})

it('preserves a newer draft even if it was edited back to empty', () => {
  const { result } = renderHook(() => useQaDraft('c1'))
  const submitted = result.current.capture()
  act(() => result.current.setDraft('new question'))
  act(() => result.current.restore('old question', submitted))
  expect(result.current.draft).toBe('new question')
  act(() => result.current.setDraft(''))
  act(() => result.current.restore('old question', submitted))
  expect(result.current.draft).toBe('')
})

it('restores an unchanged empty draft after failure', () => {
  const { result } = renderHook(() => useQaDraft('c1'))
  const submitted = result.current.capture()
  act(() => result.current.restore('failed question', submitted))
  expect(result.current.draft).toBe('failed question')
})

it('keeps unsent text across remounts and isolates conversations', () => {
  const first = renderHook(() => useQaDraft('c1'))
  act(() => first.result.current.setDraft('unfinished'))
  first.unmount()
  const second = renderHook(({ cid }) => useQaDraft(cid), { initialProps: { cid: 'c1' } })
  expect(second.result.current.draft).toBe('unfinished')
  second.rerender({ cid: 'c2' })
  expect(second.result.current.draft).toBe('')
  second.rerender({ cid: 'c1' })
  expect(second.result.current.draft).toBe('unfinished')
})

it('moves the next question to the newly created conversation', () => {
  const first = renderHook(() => useQaDraft('new'))
  act(() => first.result.current.setDraft('next question'))
  act(() => first.result.current.moveTo('conversation:c1'))
  first.unmount()
  const saved = renderHook(() => useQaDraft('conversation:c1'))
  expect(saved.result.current.draft).toBe('next question')
  act(() => window.dispatchEvent(new Event('dr:credentials-cleared')))
  expect(saved.result.current.draft).toBe('')
})

it('restores the first failed question after its new conversation replaces the temporary scope', () => {
  const hook = renderHook(({ scope }) => useQaDraft(scope), { initialProps: { scope: 'new:r' } })
  const snapshot = hook.result.current.capture()
  act(() => hook.result.current.moveTo('conversation:c1'))
  hook.rerender({ scope: 'conversation:c1' })
  act(() => hook.result.current.restore('first question', snapshot))
  expect(hook.result.current.draft).toBe('first question')
})

it('keeps edits made during conversation creation and rejects a failure from another scope', () => {
  const hook = renderHook(({ scope }) => useQaDraft(scope), { initialProps: { scope: 'new:r' } })
  const snapshot = hook.result.current.capture()
  act(() => hook.result.current.setDraft('next question'))
  act(() => hook.result.current.moveTo('conversation:c1'))
  hook.rerender({ scope: 'conversation:c1' })
  act(() => hook.result.current.restore('first question', snapshot))
  expect(hook.result.current.draft).toBe('next question')
  hook.rerender({ scope: 'conversation:c2' })
  act(() => hook.result.current.restore('first question', snapshot))
  expect(hook.result.current.draft).toBe('')
})
