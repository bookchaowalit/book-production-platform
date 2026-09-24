using UnityEngine;

namespace BookProduction.Runtime
{
    [RequireComponent(typeof(Collider2D))]
    public sealed class Collectible : MonoBehaviour
    {
        [SerializeField] private string objectiveId;
        [SerializeField] private GameProgress progress;

        public void Initialize(string id, GameProgress gameProgress)
        {
            objectiveId = id;
            progress = gameProgress;
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            if (progress == null || other.GetComponent<TopDownPlayerController>() == null)
            {
                return;
            }

            if (progress.TryCollect(objectiveId))
            {
                gameObject.SetActive(false);
            }
        }
    }
}
