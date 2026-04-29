"use client"

import React, { Component } from "react"
import { MapProvider } from "react-map-gl/maplibre"
import { MapActionProvider } from "@/lib/contexts/map-action-context"
import { ToastContainer } from "@/components/ui/toast"

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

class ErrorBoundary extends Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-screen bg-[#dce8f2] flex items-center justify-center text-slate-800">
          <div className="text-center space-y-4">
            <div className="text-[#16a34a] text-sm font-mono uppercase tracking-widest">System Error</div>
            <p className="text-slate-500 text-sm max-w-md">
              {this.state.error?.message || "An unexpected error occurred"}
            </p>
            <button
              onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload() }}
              className="px-4 py-2 rounded-lg border border-[#16a34a]/30 text-[#16a34a] text-sm hover:bg-[#16a34a]/10 transition-colors"
            >
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return (
    <ErrorBoundary>
      <MapProvider>
        <MapActionProvider>
          {children}
          <ToastContainer />
        </MapActionProvider>
      </MapProvider>
    </ErrorBoundary>
  )
}
