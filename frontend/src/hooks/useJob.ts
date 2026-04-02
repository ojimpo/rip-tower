import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { JobDetail } from "../lib/types";

export function useJob(jobId: string) {
  return useQuery<JobDetail>({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId) as Promise<JobDetail>,
    refetchInterval: 5000,
  });
}
