import React from 'react'

export const motion = new Proxy({}, {
  get: (_target: unknown, prop: string) => {
    return React.forwardRef(({ children, initial, animate, exit, transition, variants, whileHover, whileTap, layout, ...rest }: Record<string, unknown>, ref: React.Ref<unknown>) => {
      return React.createElement(prop, { ...rest, ref }, children as React.ReactNode)
    })
  }
})

export const AnimatePresence = ({ children }: { children: React.ReactNode }) => <>{children}</>
