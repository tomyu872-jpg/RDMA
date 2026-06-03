#ifndef BITMAP_RETRANS_MODULE_H
#define BITMAP_RETRANS_MODULE_H

#include <array>
#include <cstdint>
#include <deque>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "rdma-queue-pair.h"

namespace ns3 {

struct BitmapRetransFeedback {
    bool sendControl{false};
    bool isNack{false};
    bool droppedForOverflow{false};
    uint32_t ackSeq{0};
    bool hasSack{false};
    uint32_t sackSeq{0};
    uint16_t sackSize{0};
};

class BitmapRetransModule {
   public:
    void RegisterTxFlow(uint64_t key);
    void UnregisterTxFlow(uint64_t key);
    void RegisterRxFlow(uint64_t key);
    void UnregisterRxFlow(uint64_t key);

    void OnPacketSent(uint64_t key, uint32_t seq, uint32_t size, bool isRetrans);
    void OnAck(uint64_t key, uint32_t ackSeq);
    uint32_t GetOldestUnacked(uint64_t key, uint32_t fallbackSeq) const;
    bool HasOutstanding(uint64_t key) const;
    bool TryScheduleRetrans(uint64_t key, uint32_t seq);
    void ClearRetransMarker(uint64_t key, uint32_t seq);
    void ClearRetransUpTo(uint64_t key, uint32_t ackSeq);
    std::vector<uint32_t> CollectMissingSeqs(uint64_t key, uint32_t ackSeq, uint32_t sackSeq,
                                             uint16_t sackSize, uint32_t packetSize);

    BitmapRetransFeedback OnData(uint64_t key, uint32_t seq, uint32_t size, uint32_t packetSize);

   private:
    struct TxSegment {
        uint32_t seq{0};
        uint32_t size{0};
    };

    struct TxState {
        std::deque<TxSegment> outstanding;
        std::unordered_set<uint32_t> pendingRetransSeqs;
        std::unordered_set<uint32_t> sackSeqs;
    };

    struct RxState {
        uint32_t expectedSeq{0};
        std::array<uint8_t, BITMAP_SIZE> bitmap{};
    };

    std::unordered_map<uint64_t, TxState> m_txStates;
    std::unordered_map<uint64_t, RxState> m_rxStates;
};

}  // namespace ns3

#endif
