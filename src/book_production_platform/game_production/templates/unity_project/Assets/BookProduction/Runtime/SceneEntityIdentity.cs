using UnityEngine;

namespace BookProduction.Runtime
{
    public sealed class SceneEntityIdentity : MonoBehaviour
    {
        [SerializeField] private string entityId;
        [SerializeField] private string entityType;

        public string EntityId { get { return entityId; } }
        public string EntityType { get { return entityType; } }

        public void Initialize(string entityId, string entityType)
        {
            this.entityId = entityId;
            this.entityType = entityType;
        }
    }
}
