#ifndef FALCON_RETRANS_MODULE_H
#define FALCON_RETRANS_MODULE_H

#include <cstdint>
#include <map>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "ns3/nstime.h"

namespace ns3 {

class Time;

struct FalconAckInfo {
    uint32_t cumAckSeq{0};
    uint16_t bitmapBits{0};
    uint64_t bitmap{0};
    bool hasGap{false};
};

struct FalconRxResult {
    bool sendControl{false};
    FalconAckInfo ack;
};

class FalconRetransModule {
   public:
    static const uint16_t kBitmapBits = 64;

    void RegisterTxFlow(uint64_t key);
    void UnregisterTxFlow(uint64_t key);
    void RegisterRxFlow(uint64_t key);
    void UnregisterRxFlow(uint64_t key);

    void OnPacketSent(uint64_t key, uint32_t seq, uint32_t size, Time now, bool isRetrans);

    FalconRxResult OnData(uint64_t key, uint32_t seq, uint32_t size, uint32_t packetSize);

    std::vector<uint32_t> OnAck(uint64_t key, uint32_t cumAckSeq, uint16_t bitmapBits,
                                uint64_t bitmap, uint32_t packetSize, Time now, Time reoWnd);

    bool MarkRetransPending(uint64_t key, uint32_t seq);
    void ClearRetransPending(uint64_t key, uint32_t seq);
    void ClearAcked(uint64_t key, uint32_t cumAckSeq);
    uint32_t GetOldestOutstanding(uint64_t key, uint32_t fallbackSeq) const;
    uint64_t GetSelectiveAckedBytes(uint64_t key) const;

   private:
    struct TxSegmentState {
        uint32_t seq{0};
        uint32_t size{0};
        Time lastSent;
    };

    struct TxState {
        std::map<uint32_t, TxSegmentState> outstanding;
        std::unordered_set<uint32_t> retransPending;
        uint64_t selectiveAckedBytes{0};
    };

    struct RxState {
        uint32_t expectedSeq{0};
        std::unordered_set<uint32_t> bufferedPsns;
    };

    FalconAckInfo BuildAck(const RxState &state, uint32_t packetSize) const;

    std::unordered_map<uint64_t, TxState> m_txStates;
    std::unordered_map<uint64_t, RxState> m_rxStates;
};

}  // namespace ns3

#endif
