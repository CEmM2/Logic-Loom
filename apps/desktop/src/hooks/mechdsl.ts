import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getMechdslRuntime, getMechdslStatus, installTaichi } from "../api/mechdsl";

export function useMechdslStatus() {
  return useQuery({
    queryKey: ["mechdsl", "status"],
    queryFn: getMechdslStatus,
    staleTime: 60_000,
  });
}

// Taichi runtime status. Polls every 1.5s while an install is in flight, idle
// otherwise. Used by the MechDSL Overview "Install taichi" button.
export function useMechdslRuntime() {
  return useQuery({
    queryKey: ["mechdsl", "runtime"],
    queryFn: getMechdslRuntime,
    refetchInterval: (query) =>
      query.state.data?.install_status === "installing" ? 1500 : false,
  });
}

export function useInstallTaichi() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: installTaichi,
    onSuccess: (data) => {
      // Seed the runtime cache so polling starts from "installing" immediately,
      // and refresh status so verify_available flips once taichi lands.
      qc.setQueryData(["mechdsl", "runtime"], data);
      void qc.invalidateQueries({ queryKey: ["mechdsl", "status"] });
    },
  });
}
