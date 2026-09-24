using UnityEngine;

namespace BookProduction.Runtime
{
    [RequireComponent(typeof(Collider2D))]
    public sealed class ExitGate : MonoBehaviour
    {
        [SerializeField] private string exitId;
        [SerializeField] private GameProgress progress;

        public void Initialize(string id, GameProgress gameProgress)
        {
            exitId = id;
            progress = gameProgress;
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            if (progress != null && other.GetComponent<TopDownPlayerController>() != null)
            {
                progress.TryWin(exitId);
            }
        }
    }
}
