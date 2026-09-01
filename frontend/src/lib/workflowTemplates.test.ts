import {
  USER_FACING_BUILTIN_NAMES,
  isCustomWorkflow,
  isUserFacingBuiltin,
  isUserFacingWorkflow,
} from './workflowTemplates'

describe('workflow template visibility policy', () => {
  it('keeps the public builtin catalog intentionally small', () => {
    expect(USER_FACING_BUILTIN_NAMES).toEqual(['deep', 'quick', 'hsi_review'])
    expect(isUserFacingBuiltin('guarded')).toBe(false)
    expect(isUserFacingBuiltin('teams')).toBe(false)
  })

  it('treats custom workflows as user choices without exposing internal builtins', () => {
    expect(isUserFacingWorkflow({ name: 'my-flow', custom: 'True' })).toBe(true)
    expect(isUserFacingWorkflow({ name: 'my-flow', custom: true })).toBe(true)
    expect(isUserFacingWorkflow({ name: 'guarded', custom: 'False' })).toBe(false)
    expect(isUserFacingWorkflow({ name: 'deep', custom: true })).toBe(true)
  })

  it('normalizes serialized custom flags at the UI boundary', () => {
    expect(isCustomWorkflow({ custom: ' TRUE ' })).toBe(true)
    expect(isCustomWorkflow({ custom: 'false' })).toBe(false)
    expect(isCustomWorkflow({})).toBe(false)
  })
})
