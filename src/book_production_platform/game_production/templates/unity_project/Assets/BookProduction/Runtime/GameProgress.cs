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
        [SerializeField] private AudioSource collectSound;

        public int TotalObjectives { get { return totalObjectives; } }
        public bool HasCollectSound { get { return collectSound != null && collectSound.clip != null; } }
        public int CollectSoundCount { get; private set; }
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
            CollectSoundCount = 0;
        }

        public bool TryCollect(string id)
        {
            if (!collectedIds.Add(id))
            {
                return false;
            }

            Events.Add("objective_collected:" + id);
            if (HasCollectSound)
            {
                collectSound.PlayOneShot(collectSound.clip);
                CollectSoundCount += 1;
            }
            return true;
        }

        public void ConfigureCollectSound(AudioSource source)
        {
            collectSound = source;
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
