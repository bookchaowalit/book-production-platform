using System.Collections.Generic;
using UnityEngine;

namespace BookProduction.Runtime
{
    public sealed class GameProgress : MonoBehaviour
    {
        private readonly HashSet<string> collectedIds = new HashSet<string>();
        private bool movementRecorded;
        [SerializeField] private int totalObjectives;
        [SerializeField] private bool isWon;

        public int TotalObjectives { get { return totalObjectives; } }
        public int CollectedCount { get { return collectedIds.Count; } }
        public bool IsWon { get { return isWon; } }
        public List<string> Events { get; private set; } = new List<string>();

        public void Initialize(int totalObjectives)
        {
            this.totalObjectives = totalObjectives;
            collectedIds.Clear();
            Events.Clear();
            isWon = false;
            movementRecorded = false;
        }

        public bool TryCollect(string id)
        {
            if (!collectedIds.Add(id))
            {
                return false;
            }

            Events.Add("objective_collected:" + id);
            return true;
        }

        public void TryWin(string exitId)
        {
            if (isWon)
            {
                return;
            }

            if (CollectedCount != totalObjectives)
            {
                Events.Add("exit_blocked:" + exitId);
                return;
            }

            isWon = true;
            Events.Add("game_won:" + exitId);
        }

        internal void RecordPlayerMoved()
        {
            if (movementRecorded)
            {
                return;
            }

            movementRecorded = true;
            Events.Add("player_moved");
        }
    }
}
