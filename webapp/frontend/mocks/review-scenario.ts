/**
 * Web-review entry point. The current DTO corpus remains in the long-lived
 * Task 7 fixture while its route matching is shared by browser review mode.
 */
export {
  apiScenario,
  handleReviewApiRequest,
  readReviewVideo,
  type ApiScenario,
  type ReviewApiRequest,
  type ReviewApiResponse,
} from "../fixtures/task7-fixtures";
