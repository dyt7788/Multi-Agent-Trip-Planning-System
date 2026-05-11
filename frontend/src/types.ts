export interface User {
  id: string
  username: string
  email: string
}

export interface AuthTokens {
  accessToken: string
  refreshToken: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
}

// Backend API types
export interface TripPlanRequest {
  destination: string
  days: number
  budget?: number
  preferences: string[]
  preference_details: Record<string, unknown>
  mode: '初次规划' | '修改报告' | 'plan_trip' | 'modify_report'
  travel_style?: string
  start_date?: string
  user_id: string
  conversation_id?: string
  free_text: string
  image_urls: string[]
  xhs_post_urls: string[]
  language: string
  export_pdf: boolean
}

export interface AgentTrace {
  agent: string
  status: 'started' | 'completed' | 'skipped' | 'failed'
  message: string
  started_at?: string
  finished_at?: string
  metadata: Record<string, unknown>
}

export interface TimeSlot {
  time: string
  title: string
  category: string
  description: string
  estimated_cost: number
  duration_minutes: number
  source?: string
}

export interface DayItinerary {
  day: number
  date?: string
  theme: string
  slots: TimeSlot[]
  route_notes: string
  daily_budget: number
}

export interface LocationPoint {
  longitude?: number
  latitude?: number
}

export interface HotelRecommendation {
  name: string
  address: string
  location: LocationPoint
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost: number
}

export interface AttractionDetail {
  name: string
  address: string
  location: LocationPoint
  visit_duration: number
  description: string
  category: string
  ticket_price: number
}

export interface MealPlan {
  type: 'breakfast' | 'lunch' | 'dinner'
  name: string
  description: string
  estimated_cost: number
}

export interface DailyWeatherInfo {
  date?: string
  day_weather: string
  night_weather: string
  day_temp?: number
  night_temp?: number
  wind_direction: string
  wind_power: string
}

export interface DetailedDayPlan {
  date?: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel: HotelRecommendation
  attractions: AttractionDetail[]
  meals: MealPlan[]
}

export interface BudgetSummary {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface DetailedTravelPlan {
  city: string
  start_date?: string
  end_date?: string
  days: DetailedDayPlan[]
  weather_info: DailyWeatherInfo[]
  overall_suggestions: string
  budget: BudgetSummary
}

export interface ItineraryPlan {
  trip_id: string
  destination: string
  days: number
  summary: string
  itinerary: DayItinerary[]
  highlights: string[]
  restaurants: string[]
  packing_tips: string[]
  risk_notes: string[]
  total_budget?: number
  source_references: string[]
  detailed_plan?: DetailedTravelPlan
}

export interface WebSource {
  title: string
  url: string
  snippet: string
  source_type: string
  score: number
}

export interface TravelPlanResponse {
  success: boolean
  message: string
  total: unknown
  strategy: unknown
  analysis: unknown
  report: unknown
  plan: ItineraryPlan
  query: {
    destination: string
    sources: WebSource[]
    extracted: {
      destination: string
      summary: string
      attractions: string[]
      restaurants: string[]
      activities: string[]
      route_suggestions: string[]
      tips: string[]
    }
  }
  images: unknown
  preference: unknown
  reports: unknown[]
  trace: AgentTrace[]
}

export interface ReportArtifact {
  type: 'html' | 'pdf' | 'json'
  path: string
  url: string
  generated_at: string
}

export interface Conversation {
  id: string
  title: string
  userId: string
  messages: Message[]
  createdAt: string
  updatedAt: string
  destination?: string
  days?: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  trace?: AgentTrace[]
  plan?: ItineraryPlan
  reports?: ReportArtifact[]
  timestamp: string
}
